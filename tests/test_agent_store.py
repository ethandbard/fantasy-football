"""
The agent's SQLite tables: runs, wakeups, asks, the audit log, users, and
the friends' question quota. A real temp database per test.
"""
import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
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
    ok = True
    dry_run = False
    status = 200
    code = None
    message = "transaction EXECUTED"
    transaction_id = "tx1"

    def summary(self):
        return f"{'OK' if self.ok else 'REJECTED'} {self.message}"


def test_runs_lifecycle(store):
    run_id = store.create_run("pregame", trigger="wakeup", params={"a": 1}, status="queued")
    assert store.get_run(run_id)["status"] == "queued"
    store.start_run(run_id)
    assert store.get_run(run_id)["status"] == "running"
    store.finish_run(run_id, "done", result="brief", num_turns=4, cost_usd=0.1, searches=2)
    row = store.get_run(run_id)
    assert row["status"] == "done" and row["num_turns"] == 4 and row["searches"] == 2
    assert store.recent_runs(1)[0]["id"] == run_id


def test_stale_runs_are_aborted_on_startup(store):
    run_id = store.create_run("plan")
    store.mark_stale_runs()
    assert store.get_run(run_id)["status"] == "aborted"


def test_wakeups_are_idempotent_and_fire_when_due(store):
    now = datetime.now(timezone.utc)
    soon = now + timedelta(minutes=5)
    past = now - timedelta(minutes=5)
    store.add_wakeup("pregame", soon, params={"players": ["A"]}, label="later")
    store.add_wakeup("pregame", soon, params={"players": ["A"]}, label="later")
    store.add_wakeup("pregame", past, params={"players": ["B"]}, label="now")
    assert len(store.pending_wakeups()) == 2
    due = store.due_wakeups()
    assert len(due) == 1 and due[0]["label"] == "now"
    store.set_wakeup_status(due[0]["id"], "fired", "run1")
    assert len(store.pending_wakeups()) == 1
    store.clear_pending_wakeups("pregame")
    assert store.pending_wakeups() == []


def test_asks_expire_and_resolve(store):
    ask_id = store.create_ask("trade_propose", "offer X for Y", {"type": "TRADE_PROPOSAL"}, reason="value", expiry_hours=1)
    assert store.get_ask(ask_id)["status"] == "pending"
    assert [a["id"] for a in store.unposted_asks()] == [ask_id]
    store.set_ask_message(ask_id, 12345)
    assert store.ask_by_message(12345)["id"] == ask_id
    assert store.unposted_asks() == []
    store.resolve_ask(ask_id, "approved", "by owner")
    assert store.get_ask(ask_id)["status"] == "approved"
    old = store.create_ask("drop", "drop Z", {}, expiry_hours=0)
    store.expire_asks(datetime.now(timezone.utc) + timedelta(seconds=1))
    assert store.get_ask(old)["status"] == "expired"


def test_transaction_log_and_daily_failure_gate(store):
    store.log_transaction("lineup", {"type": "ROSTER"}, _Result(), description="swap", run_id="r1")
    assert store.failed_writes_today() == []
    bad = _Result()
    bad.ok = False
    bad.code = "TRAN_LINEUP_LOCKED"
    store.log_transaction("lineup", {"type": "ROSTER"}, bad, description="swap2")
    assert len(store.failed_writes_today("lineup")) == 1
    assert store.failed_writes_today("waiver") == []
    good = _Result()
    good.transaction_id = "prop9"
    store.log_transaction("trade_propose", {}, good)
    assert "prop9" in store.agent_proposed_trade_ids()


def test_users_and_question_quota(store):
    store.claim_team("u1", 4, "Chris")
    assert store.team_for_user("u1")["team_id"] == 4
    assert store.team_for_user("nobody") is None
    assert store.asks_today("u1") == 0
    store.log_ask("u1", 4, "who do I start", "r1")
    store.log_ask("u1", 4, "flex?", "r2")
    store.log_ask("u2", 5, "hi", "r3")
    assert store.asks_today("u1") == 2 and store.asks_today() == 3


def test_offers_and_notes(store):
    assert not store.offer_seen("o1")
    store.mark_offer_seen("o1")
    assert store.offer_seen("o1")
    store.set_note("canary", {"ok": True})
    assert store.get_note("canary") == {"ok": True}
    assert store.get_note("missing", "d") == "d"


class _Ctx:
    """Just enough of EspnContext for the ledger: what ESPN lists as pending, and my roster."""

    def __init__(self, pending, mine):
        self._pending = pending
        self._mine = mine

    def pending_transactions(self):
        return self._pending

    def my_roster(self, refresh=False):
        return [SimpleNamespace(player_id=p) for p in self._mine]


def _proposal(store, tid, gives, description):
    r = _Result()
    r.transaction_id = tid
    items = [{"playerId": p, "type": "TRADE", "fromTeamId": 11, "toTeamId": 8} for p in gives]
    items.append({"playerId": 99, "type": "TRADE", "fromTeamId": 8, "toTeamId": 11})
    store.log_transaction("trade_propose", {"type": "TRADE_PROPOSAL", "items": items}, r, description=description)


def test_expired_asks_are_reported_through_the_hook(store):
    seen = []
    store.ask_expired_hook = seen.append
    old = store.create_ask("drop", "drop Z", {}, expiry_hours=0)
    rows = store.expire_asks(datetime.now(timezone.utc) + timedelta(seconds=1))
    assert [r["id"] for r in rows] == [old] and seen[0]["id"] == old
    assert store.expire_asks(datetime.now(timezone.utc) + timedelta(seconds=1)) == []
    assert store.get_ask(old)["status"] == "expired"
    assert [a["id"] for a in store.recent_asks()] == [old]


@pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35, 0), reason="needs DROP COLUMN")
def test_init_adds_outcome_columns_to_an_older_table(store):
    with db.get_connection() as conn:
        conn.execute("ALTER TABLE agent_transactions DROP COLUMN outcome")
        conn.execute("ALTER TABLE agent_transactions DROP COLUMN outcome_at")
    store.init()
    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_transactions)")}
    assert {"outcome", "outcome_at"} <= cols


def test_record_ask_writes_the_season_log(tmp_path):
    from agent import ledger
    cfg = SimpleNamespace(data_dir=tmp_path, webhook_url=None)
    ledger.record_ask(cfg, {"id": "abc", "description": "offer X for Y"}, "executed", "OK TRADE_PROPOSAL HTTP 200")
    ledger.record_ask(cfg, {"id": "def", "description": "drop Z"}, "expired", announce=True)
    body = (tmp_path / "season-log.md").read_text(encoding="utf-8")
    assert "Ask abc (offer X for Y) was approved and sent to ESPN: OK TRADE_PROPOSAL HTTP 200" in body
    assert "Ask def (drop Z) expired unanswered." in body and "· approvals" in body


def test_proposal_outcomes_are_told_apart(store, tmp_path):
    from agent import ledger
    cfg = SimpleNamespace(team_id=11, data_dir=tmp_path, webhook_url=None)
    _proposal(store, "p-declined", [1], "offer A for Z")
    _proposal(store, "p-accepted", [3], "offer C for Z")
    _proposal(store, "p-expired", [4], "offer D for Z")
    _proposal(store, "p-reversed", [6], "offer F for Z")
    live = [{"id": "p-declined", "expires": "2099-01-01T00:00+00:00", "accepted": False},
            {"id": "p-accepted", "expires": "2099-01-01T00:00+00:00", "accepted": False},
            {"id": "p-expired", "expires": "2000-01-01T00:00+00:00", "accepted": False},
            {"id": "p-reversed", "expires": "2099-01-01T00:00+00:00", "accepted": True}]
    # All four still open: nothing is decided, their expiry and acceptance are remembered.
    assert ledger.check_proposals(cfg, _Ctx(live, mine=[1, 3, 4, 6])) == []
    assert len(store.unresolved_proposals()) == 4
    # All four vanish. Player 3 left my roster, so that one went through.
    got = dict(ledger.check_proposals(cfg, _Ctx([], mine=[1, 4, 6, 99])))
    assert got == {"p-declined": "declined", "p-accepted": "accepted", "p-expired": "expired", "p-reversed": "reversed"}
    assert store.unresolved_proposals() == []
    outcomes = {t["transaction_id"]: t["outcome"] for t in store.recent_transactions(10)}
    assert outcomes["p-accepted"] == "accepted" and outcomes["p-declined"] == "declined"
    body = (tmp_path / "season-log.md").read_text(encoding="utf-8")
    assert "p-declin was declined by the other side: offer A for Z." in body
    assert "p-accept went through: offer C for Z." in body
    assert "p-expire expired unanswered" in body and "vetoed or reversed" in body
    # Nothing left to check, so nothing is written twice.
    assert ledger.check_proposals(cfg, _Ctx([], mine=[1])) == []


def _claim(add_id, drop_id):
    return {"type": "ROSTER", "items": [{"playerId": add_id, "type": "ADD", "toTeamId": 11},
                                        {"playerId": drop_id, "type": "DROP", "fromTeamId": 11}]}


def test_fallback_claims_chain_onto_the_primary_ask(store):
    ask_id = store.create_ask("waiver", "add Tyler Shough (QB NO) via waiver claim, drop Jaxson Dart", _claim(1, 9))
    store.append_to_ask(ask_id, _claim(2, 9), "add Jared Goff (QB DET) via waiver claim, drop Jaxson Dart")
    ask = store.append_to_ask(ask_id, _claim(3, 9), "add Bryce Young (QB CAR) via waiver claim, drop Jaxson Dart")
    payload = json.loads(ask["payload"])
    assert [p["items"][0]["playerId"] for p in payload["chain"]] == [1, 2, 3]
    assert ask["description"] == ("add Tyler Shough (QB NO) via waiver claim, drop Jaxson Dart; "
                                  "fallbacks sharing the drop: Jared Goff (QB DET), Bryce Young (QB CAR)")
    store.resolve_ask(ask_id, "rejected", "no")
    assert store.append_to_ask(ask_id, _claim(4, 9), "late") is None


def test_waiver_asks_get_the_next_espn_run_as_their_deadline():
    from agent import deadlines, rules as rules_mod
    r = dict(rules_mod.DEFAULTS)
    # Created Tuesday 8:03 AM ET (12:03 UTC), expires 24h later: the deadline is Wednesday 3:00 AM ET.
    ask = {"kind": "waiver", "created_at": "2026-09-22T12:03:17+00:00", "expires_at": "2026-09-23T12:03:17+00:00"}
    d = deadlines.describe(ask, r)
    assert d["deadline"] == "2026-09-23T03:00-04:00" and d["deadline_note"] == "Wed 03:00 AM ET, when ESPN runs waivers"
    # Created Monday night: Tuesday has no run, so Wednesday.
    monday = {"kind": "waiver", "created_at": "2026-09-22T01:00:00+00:00", "expires_at": "2026-09-25T01:00:00+00:00"}
    assert deadlines.describe(monday, r)["deadline"].startswith("2026-09-23T03:00")
    # Anything else keeps its expiry.
    trade = {"kind": "trade_propose", "created_at": "2026-09-22T12:03:17+00:00", "expires_at": "2026-09-23T12:03:17+00:00"}
    assert deadlines.describe(trade, r)["deadline_note"].endswith("when it expires")
    # Reminder: due inside the window, not before.
    assert not deadlines.due_for_reminder(ask, r, now=datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc))
    assert deadlines.due_for_reminder(ask, r, now=datetime(2026, 9, 23, 4, 30, tzinfo=timezone.utc))


def test_reminders_fire_once_per_ask(store, tmp_path, monkeypatch):
    from agent import ledger, rules as rules_mod
    from agent import discord_out
    sent = []
    monkeypatch.setattr(discord_out, "line", lambda cfg, text, kind="info": sent.append(text))
    cfg = SimpleNamespace(data_dir=tmp_path, webhook_url=None)
    ask_id = store.create_ask("lineup", "swap", {"type": "ROSTER", "items": []}, expiry_hours=2)
    r = dict(rules_mod.DEFAULTS)
    assert ledger.remind_asks(cfg, r) == [ask_id] and ask_id in sent[0] and "when it expires" in sent[0]
    assert ledger.remind_asks(cfg, r) == [] and len(sent) == 1
    far = store.create_ask("lineup", "later", {"type": "ROSTER", "items": []}, expiry_hours=48)
    assert ledger.remind_asks(cfg, r) == []
    assert far not in sent


def test_a_chained_ask_posts_each_claim(store, tmp_path, monkeypatch):
    from agent import approvals
    posted = []

    class _Writer:
        def post(self, payload):
            posted.append(payload["items"][0]["playerId"])
            r = _Result()
            r.transaction_id = f"tx{len(posted)}"
            return r

    class _FakeCtx:
        scoring_period = 3
        writer = _Writer()

        def __init__(self, cfg, rules, write_enabled=True):
            self.cfg = cfg

        def my_roster(self, refresh=False):
            return [SimpleNamespace(player_id=9, name="Jaxson Dart", slot_id=0, lineup_locked=False)]

    monkeypatch.setattr(approvals, "EspnContext", _FakeCtx)
    cfg = SimpleNamespace(team_id=11, data_dir=tmp_path, webhook_url=None)
    ask_id = store.create_ask("waiver", "add Tyler Shough (QB NO) via waiver claim, drop Jaxson Dart",
                              dict(_claim(1, 9), scoringPeriodId=3))
    store.append_to_ask(ask_id, dict(_claim(2, 9), scoringPeriodId=3), "add Jared Goff (QB DET) via waiver claim, drop Jaxson Dart")
    ok, message = approvals.execute_ask(cfg, {}, store.get_ask(ask_id))
    assert ok and posted == [1, 2] and "Shough" in message and "Goff" in message
    assert store.get_ask(ask_id)["status"] == "executed"
    assert {t["description"] for t in store.recent_transactions(5)} == {
        "add Tyler Shough (QB NO) via waiver claim, drop Jaxson Dart",
        "add Jared Goff (QB DET) via waiver claim, drop Jaxson Dart"}


def test_espn_refusals_teach_the_undroppable_list(store):
    assert store.undroppable_ids() == set()
    store.remember_undroppable(4426348)
    store.remember_undroppable("4426348")
    assert store.undroppable_ids() == {4426348}


def test_a_rejection_blocks_only_the_same_move():
    from agent.tools.write import retry_of
    failed = [{"code": "TRAN_ROSTER_PLAYER_NOT_DROPPABLE", "message": "undroppable",
               "payload": json.dumps({"items": [{"playerId": 1, "type": "ADD"}, {"playerId": 9, "type": "DROP"}]})}]
    same = {"items": [{"playerId": 9, "type": "DROP", "fromTeamId": 11}, {"playerId": 1, "type": "ADD", "toTeamId": 11}]}
    other = {"items": [{"playerId": 1, "type": "ADD"}, {"playerId": 2, "type": "DROP"}]}
    assert retry_of(failed, same) is not None
    assert retry_of(failed, other) is None
    assert retry_of([], same) is None
