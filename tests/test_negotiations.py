"""
The negotiation ledger and the general manager's inbox: one thread per
manager that the service appends events to, and notes the workers leave.
"""
import importlib
import json
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


def test_threads_upsert_fields_and_keep_a_history(store):
    assert store.get_negotiation(9) is None
    row = store.upsert_negotiation(9, target="A.J. Brown", next_action="re-approach with a back", status=None)
    assert row["status"] == "open" and row["target"] == "A.J. Brown" and row["history"] == []
    store.add_negotiation_event(9, "we offered Gordon for Brown")
    store.add_negotiation_event(9, "our offer was declined")
    row = store.upsert_negotiation(9, status="paused", their_asks="Gibbs")
    assert [h["event"] for h in row["history"]] == ["we offered Gordon for Brown", "our offer was declined"]
    assert row["status"] == "paused" and row["target"] == "A.J. Brown" and row["their_asks"] == "Gibbs"
    assert [r["team_id"] for r in store.list_negotiations()] == [9]
    assert store.list_negotiations("open") == []
    # Events on a team with no thread create one.
    store.add_negotiation_event(4, "he offered Kittle for Boston")
    assert store.get_negotiation(4)["status"] == "open"


def test_history_is_capped(store):
    for i in range(50):
        store.add_negotiation_event(6, f"event {i}", keep=40)
    assert len(store.get_negotiation(6)["history"]) == 40
    assert store.get_negotiation(6)["history"][0]["event"] == "event 10"


def test_inbox_is_read_once(store):
    store.add_inbox("pregame", "Rice is out for weeks")
    store.add_inbox("wire", "T9 dropped Judkins")
    rows = store.unread_inbox()
    assert [r["text"] for r in rows] == ["Rice is out for weeks", "T9 dropped Judkins"]
    assert store.unread_inbox() == []
    store.add_inbox("lineup", "later note")
    assert [r["text"] for r in store.unread_inbox(mark=False)] == ["later note"]
    assert len(store.unread_inbox()) == 1


def test_ledger_records_proposal_outcomes_on_the_thread(store, monkeypatch, tmp_path):
    from agent import ledger
    monkeypatch.setattr(ledger, "store", store)
    monkeypatch.setattr(ledger.discord_out, "line", lambda *a, **k: None)
    payload = {"items": [{"playerId": 1, "type": "TRADE", "fromTeamId": 11, "toTeamId": 9},
                         {"playerId": 2, "type": "TRADE", "fromTeamId": 9, "toTeamId": 11}]}
    result = SimpleNamespace(ok=True, dry_run=False, status=200, code=None, message="ok", transaction_id="abc12345")
    store.log_transaction("trade_propose", payload, result, description="offer Gordon to bucs for Brown")
    cfg = SimpleNamespace(team_id=11, data_dir=tmp_path)
    ctx = SimpleNamespace(pending_transactions=lambda: [], my_roster=lambda refresh=False: [SimpleNamespace(player_id=1)])
    outcomes = ledger.check_proposals(cfg, ctx)
    assert outcomes == [("abc12345", "declined")]
    events = [h["event"] for h in store.get_negotiation(9)["history"]]
    assert events == ["our offer was declined by the other side: offer Gordon to bucs for Brown"]
    offer = {"id": "deadbeef-1", "team_id": 10, "items": [
        {"player": "Olave", "from_team_id": 10, "to_team_id": 11}, {"player": "Gibbs", "from_team_id": 11, "to_team_id": 10}]}
    ledger.record_incoming(cfg, offer)
    assert store.get_negotiation(10)["history"][0]["event"] == "he offered Olave for our Gibbs (offer deadbeef)"
