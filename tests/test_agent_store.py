"""
The agent's SQLite tables: runs, wakeups, asks, the audit log, users, and
the friends' question quota. A real temp database per test.
"""
import importlib
from datetime import datetime, timedelta, timezone

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
