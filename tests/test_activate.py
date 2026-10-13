"""IR activation planning: moving a healthy player out of the IR slot.

Covers the week-5 failure where Jayden Daniels sat healthy in IR while the
roster was full: ESPN rejects a lone IR->active move (TRAN_ROSTER_LIMIT_EXCEEDED_ONE),
so the safe sequence is drop-first-then-move, which plan_activate plans.
"""
import gamedaybot.espn.roster as roster
from agent.tools.write import plan_activate

IR, BE, QB, WR = 21, 20, 0, 4
DANIELS, SHOUGH, BTJ = 4426348, 4360689, 4432773
COUNTS = {0: 1, 2: 2, 4: 2, 6: 1, 16: 1, 23: 1}


def _entry(name, pid, slot, position="QB", eligible=None, injury="ACTIVE", locked=False):
    return roster.RosterEntry(
        player_id=pid, name=name, position=position, pro_team="WSH", slot_id=slot,
        slot=roster.slot_name(slot), eligible_slot_ids=eligible or [0, 20, 21],
        eligible_slots=["QB", "BE", "IR"], injury_status=injury,
        injured=injury not in ("ACTIVE",),
        lineup_locked=False, roster_locked=locked, trade_locked=False, droppable=True,
    )


def _full_roster():
    """16 non-IR players (a full roster) plus Daniels healthy in IR."""
    entries = [_entry("Shough", SHOUGH, QB)]
    for i in range(15):
        entries.append(_entry(f"Bench{i}", 100 + i, BE, position="WR", eligible=[4, 20, 21]))
    entries.append(_entry("Daniels", DANIELS, IR, injury="ACTIVE"))
    return entries


def test_full_roster_plans_drop_then_move_with_extra_benching():
    # The Oct 9 scenario: Daniels IR->QB, Shough QB->BE, drop BTJ-equivalent first.
    entries = _full_roster()
    plan, problems = plan_activate(entries, DANIELS, QB, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert problems == []
    assert plan["need_drop"] is True
    assert plan["drop_ids"] == [100]
    assert plan["to_slot_id"] == QB
    assert "Daniels" in plan["move_desc"] and "Shough" in plan["move_desc"]


def test_roster_with_room_needs_no_drop():
    entries = _full_roster()[:15]  # 15 non-IR players: one open spot
    entries.append(_entry("Daniels", DANIELS, IR, injury="ACTIVE"))
    plan, problems = plan_activate(entries, DANIELS, QB, [], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert problems == []
    assert plan["need_drop"] is False
    assert plan["drop_ids"] == []


def test_full_roster_without_drop_ids_is_a_problem():
    plan, problems = plan_activate(_full_roster(), DANIELS, QB, [], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None
    assert any("drop" in p for p in problems)


def test_occupied_target_slot_without_extra_move_is_a_problem():
    # Shough still holds QB: the move alone would double-fill the slot.
    plan, problems = plan_activate(_full_roster(), DANIELS, QB, [100], COUNTS)
    assert plan is None
    assert any("QB" in p for p in problems)


def test_player_not_in_ir_is_a_problem():
    plan, problems = plan_activate(_full_roster(), SHOUGH, BE, [], COUNTS)
    assert plan is None
    assert any("IR slot" in p for p in problems)


def test_unknown_player_is_a_problem():
    plan, problems = plan_activate(_full_roster(), 999999, QB, [], COUNTS)
    assert plan is None


def test_cannot_drop_the_activated_player():
    plan, problems = plan_activate(_full_roster(), DANIELS, QB, [DANIELS], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None


def test_locked_drop_candidate_is_a_problem():
    entries = _full_roster()
    entries[1] = _entry("Bench0", 100, BE, position="WR", eligible=[4, 20, 21], locked=True)
    plan, problems = plan_activate(entries, DANIELS, QB, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None
    assert any("locked" in p for p in problems)


def test_ineligible_target_slot_is_a_problem():
    plan, problems = plan_activate(_full_roster(), DANIELS, 2, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None
    assert any("eligible" in p for p in problems)


def test_target_cannot_be_ir():
    plan, problems = plan_activate(_full_roster(), DANIELS, IR, [100], COUNTS)
    assert plan is None


def test_preexisting_ir_problem_does_not_block():
    # Another healthy player stuck in IR is a pre-existing problem, not ours.
    entries = _full_roster()
    entries.append(_entry("Other", 999, IR, injury="ACTIVE"))
    plan, problems = plan_activate(entries, DANIELS, QB, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert problems == []
    assert plan["need_drop"] is True


def test_locked_player_cannot_be_activated():
    entries = _full_roster()
    entries[-1] = _entry("Daniels", DANIELS, IR, injury="ACTIVE")
    entries[-1].lineup_locked = True
    plan, problems = plan_activate(entries, DANIELS, QB, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None
    assert any("locked" in p for p in problems)


def test_locked_starter_cannot_be_benched_by_an_extra_move():
    entries = _full_roster()
    entries[0].lineup_locked = True
    plan, problems = plan_activate(entries, DANIELS, QB, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None
    assert any("Shough" in p and "locked" in p for p in problems)


# ------------------------------------------------- execute_activate end to end

import asyncio
import importlib
from types import SimpleNamespace

import pytest

import gamedaybot.storage.db as db


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    importlib.reload(db)
    import agent.store as store_mod
    importlib.reload(store_mod)
    store_mod.init()
    yield store_mod
    importlib.reload(db)


class _Result:
    def __init__(self, ok=True, message="transaction EXECUTED"):
        self.ok, self.message = ok, message
        self.dry_run, self.status, self.code, self.transaction_id = False, 200 if ok else 409, None if ok else "TRAN_X", "tx"

    def summary(self):
        return f"{'OK' if self.ok else 'REJECTED'} {self.message}"


class _FakeEspn:
    """A roster that ESPN-style posts actually change, so the post-drop rebuild sees the drop."""

    def __init__(self, entries, reject_kinds=()):
        self.entries = entries
        self.posted = []
        self.reject_kinds = set(reject_kinds)

    def post(self, payload):
        kind = payload.get("type")
        self.posted.append(payload)
        if kind in self.reject_kinds:
            return _Result(ok=False, message="rejected")
        for item in payload["items"]:
            pid = int(item["playerId"])
            if item.get("type") == "DROP":
                self.entries = [e for e in self.entries if int(e.player_id) != pid]
            elif "toLineupSlotId" in item:
                for e in self.entries:
                    if int(e.player_id) == pid:
                        e.slot_id = int(item["toLineupSlotId"])
                        e.slot = roster.slot_name(e.slot_id)
        return _Result()


def _tools(espn):
    from agent.tools import write as write_tools
    ctx = SimpleNamespace(
        cfg=SimpleNamespace(team_id=11, swid="{SWID}"), rules={}, scoring_period=6, writer=espn,
        my_roster=lambda refresh=False: list(espn.entries), slot_counts=lambda: COUNTS,
        rosters=lambda refresh=False: None)
    run = SimpleNamespace(run_id="r1", transactions=[], asks=[], adds_executed=0)
    return {t.name: t.handler for t in write_tools.build(ctx, run)}


def _call(handler, args):
    out = asyncio.run(handler(args))
    return out["content"][0]["text"], out.get("is_error", False)


def _activate_args():
    return {"player_id": DANIELS, "to_slot": "QB", "drop_player_ids": [100],
            "extra_moves": [{"player_id": SHOUGH, "to_slot": "BE"}]}


def test_execute_drops_then_activates_and_benches_the_starter(store):
    espn = _FakeEspn(_full_roster())
    tools = _tools(espn)
    preview, is_err = _call(tools["preview_activate"], _activate_args())
    assert not is_err, preview
    token = preview.split("Token: ")[1].split()[0]
    result, is_err = _call(tools["execute_activate"], {"token": token, "reason": "test"})
    assert not is_err, result
    assert len(espn.posted) == 2
    assert espn.posted[0]["items"] == [{"playerId": 100, "type": "DROP", "fromTeamId": 11}]
    moved = {i["playerId"]: i["toLineupSlotId"] for i in espn.posted[1]["items"]}
    assert moved == {DANIELS: QB, SHOUGH: BE}
    slots = {int(e.player_id): e.slot_id for e in espn.entries}
    assert slots[DANIELS] == QB and slots[SHOUGH] == BE and 100 not in slots


def test_rejected_drop_never_posts_the_move(store):
    espn = _FakeEspn(_full_roster())
    tools = _tools(espn)
    preview, _ = _call(tools["preview_activate"], _activate_args())
    token = preview.split("Token: ")[1].split()[0]
    espn.reject_kinds = {espn_drop_type()}
    result, is_err = _call(tools["execute_activate"], {"token": token})
    assert is_err and "Activation aborted" in result
    assert len(espn.posted) == 1


def espn_drop_type():
    import gamedaybot.espn.writes as writes
    return writes.add_drop_payload(11, "{SWID}", 6, None, [100])["type"]
