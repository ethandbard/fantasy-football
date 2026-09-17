"""
Pins the exact shape of every ESPN write envelope. The endpoint is
undocumented and deserializes strictly, so a stray key or a wrong type is a
400 in production; these tests are the contract.
"""
import pytest

import gamedaybot.espn.writes as w

TEAM, SWID, PERIOD = 11, "{ABC-123}", 3


def test_lineup_payload_only_changed_slots():
    p = w.lineup_payload(TEAM, SWID, PERIOD, [
        {"player_id": 1, "from_slot": 20, "to_slot": 2},
        {"player_id": 2, "from_slot": 2, "to_slot": 20},
    ])
    assert p["type"] == "ROSTER"
    assert p["teamId"] == 11 and p["memberId"] == "{ABC-123}"
    assert p["executionType"] == "EXECUTE"
    assert p["scoringPeriodId"] == 3
    assert p["isLeagueManager"] is False and p["isActingAsTeamOwner"] is False
    assert p["items"] == [
        {"playerId": 1, "type": "LINEUP", "fromLineupSlotId": 20, "toLineupSlotId": 2},
        {"playerId": 2, "type": "LINEUP", "fromLineupSlotId": 2, "toLineupSlotId": 20},
    ]
    assert set(p) == {"type", "teamId", "memberId", "executionType", "isLeagueManager",
                      "isActingAsTeamOwner", "scoringPeriodId", "items"}


def test_lineup_payload_rejects_same_slot_and_empty():
    with pytest.raises(ValueError):
        w.lineup_payload(TEAM, SWID, PERIOD, [{"player_id": 1, "from_slot": 2, "to_slot": 2}])
    with pytest.raises(ValueError):
        w.lineup_payload(TEAM, SWID, PERIOD, [])


def test_free_agent_add_with_drop():
    p = w.add_drop_payload(TEAM, SWID, PERIOD, add_id=500, drop_ids=[400])
    assert p["type"] == "FREEAGENT"
    assert "bidAmount" not in p
    assert p["items"] == [
        {"playerId": 500, "type": "ADD", "toTeamId": 11},
        {"playerId": 400, "type": "DROP", "fromTeamId": 11},
    ]


def test_waiver_claim_carries_bid():
    p = w.add_drop_payload(TEAM, SWID, PERIOD, add_id=500, drop_ids=[400], waiver=True)
    assert p["type"] == "WAIVER"
    assert p["bidAmount"] == 0
    p = w.add_drop_payload(TEAM, SWID, PERIOD, add_id=500, waiver=True, bid_amount=7)
    assert p["bidAmount"] == 7 and p["items"] == [{"playerId": 500, "type": "ADD", "toTeamId": 11}]


def test_standalone_drop_is_a_roster_envelope():
    p = w.add_drop_payload(TEAM, SWID, PERIOD, drop_ids=[400])
    assert p["type"] == "ROSTER"
    assert p["items"] == [{"playerId": 400, "type": "DROP", "fromTeamId": 11}]
    with pytest.raises(ValueError):
        w.add_drop_payload(TEAM, SWID, PERIOD)


def test_cancel_and_withdraw():
    p = w.cancel_payload(TEAM, SWID, PERIOD, "tx-1")
    assert p["type"] == "WAIVER" and p["executionType"] == "CANCEL"
    assert p["relatedTransactionId"] == "tx-1" and p["items"] == []
    p = w.trade_withdraw_payload(TEAM, SWID, PERIOD, "tx-2")
    assert p["type"] == "TRADE_PROPOSAL" and p["executionType"] == "CANCEL"


def test_trade_propose_both_directions():
    p = w.trade_propose_payload(TEAM, SWID, PERIOD, other_team_id=4, gives=[1, 2], gets=[9])
    assert p["type"] == "TRADE_PROPOSAL"
    assert p["items"] == [
        {"playerId": 1, "type": "TRADE", "fromTeamId": 11, "toTeamId": 4},
        {"playerId": 2, "type": "TRADE", "fromTeamId": 11, "toTeamId": 4},
        {"playerId": 9, "type": "TRADE", "fromTeamId": 4, "toTeamId": 11},
    ]
    with pytest.raises(ValueError):
        w.trade_propose_payload(TEAM, SWID, PERIOD, 4, gives=[], gets=[9])


def test_trade_respond():
    p = w.trade_respond_payload(TEAM, SWID, PERIOD, "tx-9", accept=True, drop_ids=[3])
    assert p["type"] == "TRADE_ACCEPT" and p["relatedTransactionId"] == "tx-9"
    assert p["items"] == [{"playerId": 3, "type": "DROP", "fromTeamId": 11}]
    p = w.trade_respond_payload(TEAM, SWID, PERIOD, "tx-9", accept=False, drop_ids=[3])
    assert p["type"] == "TRADE_DECLINE" and p["items"] == []


def test_swid_is_brace_wrapped():
    assert w.normalize_swid("ABC") == "{ABC}"
    assert w.normalize_swid("{ABC}") == "{ABC}"
    assert w.write_url(2026, 126899882).endswith("/seasons/2026/segments/0/leagues/126899882/transactions/")


def test_interpret_success_and_pending():
    ok, code, msg, tx = w.interpret(200, {"id": "abc", "status": "EXECUTED"})
    assert ok and code is None and tx == "abc" and "EXECUTED" in msg
    ok, code, msg, tx = w.interpret(200, {"id": "def", "status": "PENDING"})
    assert ok and tx == "def"


def test_interpret_known_rejections():
    body = {"messages": ["Lineup locked"], "details": [{"type": "TRAN_LINEUP_LOCKED", "message": "x"}]}
    ok, code, msg, _ = w.interpret(409, body)
    assert not ok and code == "TRAN_LINEUP_LOCKED" and "locked" in msg
    ok, code, msg, _ = w.interpret(401, {"details": [{"type": "AUTH_MISSING_CREDENTIALS"}]})
    assert not ok and code == "AUTH_MISSING_CREDENTIALS" and "cookies" in msg


def test_interpret_unknown_rejection_uses_messages():
    ok, code, msg, _ = w.interpret(400, {"messages": ["Invalid Input"]})
    assert not ok and code is None and msg == "Invalid Input"
    ok, code, msg, _ = w.interpret(500, "Server Error")
    assert not ok and "HTTP 500" in msg


class _FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError
        return self._body


class _FakeSession:
    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body if body is not None else {"id": "t1", "status": "EXECUTED"}

    def post(self, url, json=None, headers=None, cookies=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "cookies": cookies})
        return _FakeResponse(self.status, self.body)


def test_client_dry_run_never_posts():
    session = _FakeSession()
    client = w.WriteClient(1, 2026, "s2", "{X}", TEAM, dry_run=True, session=session)
    result = client.lineup(PERIOD, [{"player_id": 1, "from_slot": 20, "to_slot": 2}])
    assert result.dry_run and result.ok and session.calls == []
    assert "DRY RUN" in result.summary()


def test_client_live_post_sends_cookies_and_headers():
    session = _FakeSession()
    client = w.WriteClient(126899882, 2026, "s2value", "X-1", TEAM, dry_run=False, session=session)
    result = client.add_drop(PERIOD, add_id=5, drop_ids=[6])
    assert result.ok and result.transaction_id == "t1" and result.status == 200
    call = session.calls[0]
    assert call["url"] == w.write_url(2026, 126899882)
    assert call["cookies"] == {"espn_s2": "s2value", "SWID": "{X-1}"}
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"]["memberId"] == "{X-1}"


def test_client_reports_rejection_without_raising():
    session = _FakeSession(409, {"details": [{"type": "TRAN_LINEUP_LOCKED"}]})
    client = w.WriteClient(1, 2026, "s2", "{X}", TEAM, dry_run=False, session=session)
    result = client.lineup(PERIOD, [{"player_id": 1, "from_slot": 20, "to_slot": 2}])
    assert not result.ok and result.code == "TRAN_LINEUP_LOCKED" and result.status == 409
    assert "REJECTED" in result.summary()
