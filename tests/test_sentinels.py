"""
The roster integrity sentinel: the findings it raises from a roster, which
it fixes itself, which wake the lineup worker, and which wake the general
manager; plus the once-per-interval dedupe of wakeups.
"""
import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import gamedaybot.espn.roster as roster
import gamedaybot.storage.db as db
from agent import sentinels

SLOTS = {0: 1, 2: 2, 4: 2, 6: 1, 23: 2, 16: 1, 17: 1}


def _e(pid, name, pos, slot_id, eligible, tag="ACTIVE", proj=10.0, locked=False, bye=False):
    return roster.RosterEntry(player_id=pid, name=name, position=pos, pro_team="DET", slot_id=slot_id,
                              slot=roster.slot_name(slot_id), eligible_slot_ids=eligible,
                              eligible_slots=[roster.slot_name(s) for s in eligible], injury_status=tag,
                              injured=tag != "ACTIVE", lineup_locked=locked, roster_locked=False, trade_locked=False,
                              droppable=True, projected=proj, bye=bye)


QB = [0, 20, 21]
RB = [2, 23, 20, 21]
WR = [4, 23, 20, 21]
TE = [6, 23, 20, 21]


def _roster(overrides=None):
    entries = [
        _e(1, "QB A", "QB", 0, QB, proj=20),
        _e(2, "RB A", "RB", 2, RB, proj=15), _e(3, "RB B", "RB", 2, RB, proj=12),
        _e(4, "WR A", "WR", 4, WR, proj=14), _e(5, "WR B", "WR", 4, WR, proj=13),
        _e(6, "TE A", "TE", 6, TE, proj=9),
        _e(7, "FLEX A", "WR", 23, WR, proj=10), _e(8, "FLEX B", "RB", 23, RB, proj=9),
        _e(9, "DST A", "D/ST", 16, [16, 20], proj=7), _e(10, "K A", "K", 17, [17, 20], proj=8),
        _e(11, "Bench RB", "RB", 20, RB, proj=8), _e(12, "Bench WR", "WR", 20, WR, proj=6),
        _e(13, "QB B", "QB", 20, QB, proj=17),
    ]
    by_id = {e.player_id: e for e in entries}
    for pid, changes in (overrides or {}).items():
        e = by_id[pid]
        for k, v in changes.items():
            setattr(e, k, v)
    return entries


def test_a_clean_roster_raises_nothing():
    assert sentinels.inspect(_roster(), SLOTS) == []


def test_healthy_player_in_ir_is_fixed_when_the_roster_has_room():
    entries = _roster({12: {"slot_id": roster.IR_SLOT, "slot": "IR"}})
    found = sentinels.inspect(entries, SLOTS)
    assert [f.kind for f in found] == ["ir_illegal"]
    assert found[0].route == sentinels.FIX
    assert found[0].moves == [{"player_id": 12, "from_slot": roster.IR_SLOT, "to_slot": roster.BENCH_SLOT}]


def test_healthy_player_in_ir_on_a_full_roster_wakes_the_general_manager():
    entries = _roster({12: {"slot_id": roster.IR_SLOT, "slot": "IR"}})
    entries += [_e(100 + i, f"Fill {i}", "WR", 20, WR, proj=1) for i in range(4)]
    assert roster.open_spots(entries) == 0
    found = sentinels.inspect(entries, SLOTS)
    assert found[0].kind == "ir_illegal" and found[0].route == sentinels.GM
    assert "drop is needed" in found[0].text


def test_starter_on_bye_or_out_wakes_the_lineup_worker_or_the_gm():
    found = sentinels.inspect(_roster({2: {"bye": True}}), SLOTS)
    kinds = {(f.kind, f.route) for f in found}
    assert ("starter_cannot_play", sentinels.LINEUP) in kinds
    assert any("Bench RB can fill" in f.text for f in found)
    # An Out starter who is already locked is nobody's problem now.
    assert not [f for f in sentinels.inspect(_roster({2: {"injury_status": "OUT", "lineup_locked": True}}), SLOTS)
                if f.kind == "starter_cannot_play"]
    # No bench player for the slot: an add is needed, so the general manager wakes.
    found = sentinels.inspect(_roster({9: {"injury_status": "OUT"}}), SLOTS)
    dst = [f for f in found if f.kind == "starter_cannot_play"]
    assert dst and dst[0].route == sentinels.GM


def test_empty_slot_and_composition_findings():
    entries = [e for e in _roster() if e.player_id != 10]
    kinds = {f.kind: f.route for f in sentinels.inspect(entries, SLOTS)}
    assert kinds["no_k"] == sentinels.GM and kinds["empty_slot"] == sentinels.GM
    entries = _roster() + [_e(14, "QB C", "QB", 20, QB, proj=5)]
    assert "third_qb" in {f.kind for f in sentinels.inspect(entries, SLOTS, rules={"max_qb": 2})}


def test_lineup_gap_uses_model_values_and_respects_locks():
    entries = _roster()
    values = {e.player_id: e.projected for e in entries}
    values[12] = 16.0   # the model loves the bench receiver
    found = sentinels.inspect(entries, SLOTS, values)
    gap = [f for f in found if f.kind == "lineup_gap"]
    assert gap and gap[0].route == sentinels.LINEUP and "start Bench WR over" in gap[0].text
    # The swap would bench FLEX B; once he is locked there is nothing to do, so nothing fires.
    assert not [f for f in sentinels.inspect(_roster({8: {"lineup_locked": True}}), SLOTS, values) if f.kind == "lineup_gap"]
    # Under the threshold nothing fires.
    values[12] = 11.0
    assert not [f for f in sentinels.inspect(entries, SLOTS, values) if f.kind == "lineup_gap"]


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    importlib.reload(db)
    import agent.store as store_mod
    importlib.reload(store_mod)
    store_mod.init()
    monkeypatch.setattr(sentinels, "store", store_mod)
    yield store_mod
    importlib.reload(db)


class _Writer:
    def __init__(self):
        self.posted = []

    def post(self, payload):
        self.posted.append(payload)
        return SimpleNamespace(ok=True, dry_run=False, status=200, code=None, message="transaction EXECUTED",
                               transaction_id="tx", summary=lambda: "OK")


class _Queue:
    def __init__(self):
        self.submitted = []

    def submit(self, job, params=None, trigger="api"):
        self.submitted.append((job, params, trigger))
        return f"run{len(self.submitted)}"


def test_check_fixes_ir_wakes_runs_once_and_records_the_pass(fresh_store, monkeypatch, tmp_path):
    entries = _roster({12: {"slot_id": roster.IR_SLOT, "slot": "IR"}, 2: {"bye": True}})
    writer = _Writer()
    ctx = SimpleNamespace(my_roster=lambda refresh=False: entries, slot_counts=lambda: SLOTS, week=8,
                          scoring_period=8, rules={}, writer=writer)
    cfg = SimpleNamespace(team_id=11, swid="{X}", data_dir=tmp_path)
    queue = _Queue()
    posted = []
    monkeypatch.setattr(sentinels.discord_out, "line", lambda cfg, text, kind="info": posted.append(text))
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    out = sentinels.check(ctx, cfg, queue, now=now)
    assert len(writer.posted) == 1 and writer.posted[0]["items"][0]["toLineupSlotId"] == roster.BENCH_SLOT
    assert len(queue.submitted) == 1 and queue.submitted[0][0] == "lineup"
    assert "RB A starts at RB but is on bye" in queue.submitted[0][1]["findings"]
    assert out["fixed"] and posted
    # The same finding an hour later does not wake another run; after the interval it does.
    queue.submitted.clear()
    sentinels.check(ctx, cfg, queue, now=now + timedelta(hours=1))
    assert queue.submitted == []
    sentinels.check(ctx, cfg, queue, now=now + sentinels.WAKE_INTERVAL + timedelta(minutes=1))
    assert len(queue.submitted) == 1
    assert fresh_store.get_note("integrity")["findings"]
