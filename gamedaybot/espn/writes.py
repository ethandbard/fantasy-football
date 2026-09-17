"""
ESPN roster writes: lineup moves, add/drop, waiver claims, and trades.

ESPN's web app performs every roster action by POSTing one transaction
envelope to an undocumented write host. It authorizes by the same espn_s2 and
SWID cookies the read side uses, so no browser session is involved. The
payload shapes here come from three projects that exercised the endpoint
against live leagues (see fantasy-football-agents/AGENT-PLAN.md, section 1).

Design rules, each of which exists because the alternative bit someone:

- Dry run is the default. A client built without dry_run=False never POSTs.
- Payloads are built by pure functions so tests can pin their exact shape.
- ESPN deserializes strictly: an unknown or null key is a 400, so the
  builders only emit keys that carry a value.
- Writes target the league's current scoring period only.
- A non-200 is final. The caller decides whether to retry tomorrow; this
  module never retries on its own.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import requests

logger = logging.getLogger(__name__)

WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"
BENCH_SLOT = 20
IR_SLOT = 21

HEADERS = {
    "Content-Type": "application/json",
    "X-Fantasy-Source": "kona",
    "X-Fantasy-Platform": "kona-PROD",
}

# Rejection vocabulary seen in the wild, mapped to what a person should do.
ERROR_CODES = {
    "AUTH_MISSING_CREDENTIALS": "ESPN rejected the cookies. Refresh ESPN_S2 and SWID from the browser.",
    "HTTP_METHOD_NOT_SUPPORTED": "Wrong HTTP method for the write host.",
    "TRAN_LINEUP_LOCKED": "A player in this move is locked because his game has started.",
    "TRAN_INVALID_SCORINGPERIOD_NOT_CURRENT": "Writes only target the league's current scoring period.",
    "TRAN_ROSTER_SAME_SLOT": "A lineup item moved a player to the slot he already occupies.",
    "TRAN_ROSTER_INVALID": "ESPN judged the resulting roster illegal (slot counts or eligibility).",
    "TRAN_PLAYER_NOT_FREE_AGENT": "That player is not a free agent right now (on waivers or rostered).",
    "TRAN_PLAYER_ON_WAIVERS": "That player is on waivers; submit a waiver claim instead of a free-agent add.",
    "TRAN_ROSTER_FULL": "The roster is full; include a drop with the add.",
    "TRAN_TRADE_DEADLINE": "The trade deadline has passed.",
}


@dataclass
class WriteResult:
    ok: bool
    status: Optional[int]
    code: Optional[str]
    message: str
    payload: dict
    body: Any = None
    transaction_id: Optional[str] = None
    dry_run: bool = False

    def summary(self):
        head = "DRY RUN" if self.dry_run else ("OK" if self.ok else "REJECTED")
        parts = [head, self.payload.get("type", "?")]
        if self.status is not None:
            parts.append(f"HTTP {self.status}")
        if self.code:
            parts.append(self.code)
        if self.transaction_id:
            parts.append(f"id {self.transaction_id}")
        parts.append(self.message)
        return " · ".join(str(p) for p in parts if p)


def normalize_swid(swid):
    """ESPN's SWID is brace-wrapped; the write host wants it that way too."""
    swid = (swid or "").strip()
    if not swid.startswith("{"):
        swid = "{" + swid
    if not swid.endswith("}"):
        swid = swid + "}"
    return swid


def write_url(year, league_id):
    return (f"{WRITE_HOST}/apis/v3/games/ffl/seasons/{int(year)}"
            f"/segments/0/leagues/{int(league_id)}/transactions/")


# ----------------------------------------------------------------- payloads

def _envelope(kind, team_id, member_id, scoring_period, items, execution="EXECUTE", **extra):
    payload = {
        "type": kind,
        "teamId": int(team_id),
        "memberId": normalize_swid(member_id),
        "executionType": execution,
        "isLeagueManager": False,
        "isActingAsTeamOwner": False,
        "scoringPeriodId": int(scoring_period),
        "items": list(items),
    }
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload


def lineup_payload(team_id, member_id, scoring_period, moves):
    """
    moves: iterable of {player_id, from_slot, to_slot}. Only changed slots
    belong here; a same-slot item makes ESPN reject the whole envelope.
    """
    items = []
    for move in moves:
        if int(move["from_slot"]) == int(move["to_slot"]):
            raise ValueError(f"player {move['player_id']} moves to the slot he is in")
        items.append({
            "playerId": int(move["player_id"]),
            "type": "LINEUP",
            "fromLineupSlotId": int(move["from_slot"]),
            "toLineupSlotId": int(move["to_slot"]),
        })
    if not items:
        raise ValueError("a lineup transaction needs at least one move")
    return _envelope("ROSTER", team_id, member_id, scoring_period, items)


def add_drop_payload(team_id, member_id, scoring_period, add_id=None, drop_ids=(),
                     waiver=False, bid_amount=None, to_slot=None):
    """
    Add a free agent, claim a waiver player, drop one or more players, or any
    combination. With no add the envelope is a plain ROSTER drop. A waiver
    claim carries bidAmount (0 in a non-FAAB league).
    """
    items = []
    if add_id is not None:
        item = {"playerId": int(add_id), "type": "ADD", "toTeamId": int(team_id)}
        if to_slot is not None:
            item["toLineupSlotId"] = int(to_slot)
        items.append(item)
    for drop_id in drop_ids or ():
        items.append({"playerId": int(drop_id), "type": "DROP", "fromTeamId": int(team_id)})
    if not items:
        raise ValueError("an add/drop transaction needs an add or a drop")
    if add_id is None:
        return _envelope("ROSTER", team_id, member_id, scoring_period, items)
    if waiver:
        return _envelope("WAIVER", team_id, member_id, scoring_period, items,
                         bidAmount=int(bid_amount or 0))
    return _envelope("FREEAGENT", team_id, member_id, scoring_period, items)


def cancel_payload(team_id, member_id, scoring_period, related_id, kind="WAIVER"):
    """Cancel a pending claim or withdraw a proposal by its transaction id."""
    return _envelope(kind, team_id, member_id, scoring_period, [], execution="CANCEL",
                     relatedTransactionId=str(related_id))


def trade_propose_payload(team_id, member_id, scoring_period, other_team_id, gives, gets):
    """gives: my player ids going to other_team_id; gets: their player ids coming to me."""
    items = []
    for pid in gives:
        items.append({"playerId": int(pid), "type": "TRADE",
                      "fromTeamId": int(team_id), "toTeamId": int(other_team_id)})
    for pid in gets:
        items.append({"playerId": int(pid), "type": "TRADE",
                      "fromTeamId": int(other_team_id), "toTeamId": int(team_id)})
    if not gives or not gets:
        raise ValueError("a trade needs players on both sides")
    return _envelope("TRADE_PROPOSAL", team_id, member_id, scoring_period, items)


def trade_respond_payload(team_id, member_id, scoring_period, related_id, accept, drop_ids=()):
    """Accept or decline an incoming offer. Drops only apply when accepting overflows the roster."""
    kind = "TRADE_ACCEPT" if accept else "TRADE_DECLINE"
    items = []
    if accept:
        for drop_id in drop_ids or ():
            items.append({"playerId": int(drop_id), "type": "DROP", "fromTeamId": int(team_id)})
    return _envelope(kind, team_id, member_id, scoring_period, items,
                     relatedTransactionId=str(related_id))


def trade_withdraw_payload(team_id, member_id, scoring_period, related_id):
    return cancel_payload(team_id, member_id, scoring_period, related_id, kind="TRADE_PROPOSAL")


# ------------------------------------------------------------ interpretation

def interpret(status, body):
    """
    Turn an ESPN response into (ok, code, message, transaction_id).

    Success is a 200 whose body reports EXECUTED (immediate moves) or
    PENDING (waiver claims and trade proposals). Rejections carry a typed
    detail; the type is matched against ERROR_CODES for a readable message.
    """
    text = json.dumps(body) if not isinstance(body, str) else body
    code = None
    for known in ERROR_CODES:
        if known in text:
            code = known
            break
    if code is None and isinstance(body, dict):
        for detail in body.get("details") or []:
            if isinstance(detail, dict) and detail.get("type"):
                code = detail["type"]
                break
    transaction_id = None
    tx_status = None
    if isinstance(body, dict):
        transaction_id = body.get("id")
        tx_status = body.get("status")
    if status == 200 and code is None:
        message = f"transaction {tx_status or 'accepted'}"
        return True, None, message, transaction_id
    message = ERROR_CODES.get(code) if code else None
    if not message:
        if isinstance(body, dict) and body.get("messages"):
            message = "; ".join(str(m) for m in body["messages"])
        else:
            message = f"ESPN returned HTTP {status}"
    return False, code, message, transaction_id


# ------------------------------------------------------------------- client

class WriteClient:
    """
    Posts transaction envelopes for one team. Dry run by default: the
    payload is built and returned but nothing is sent.
    """

    def __init__(self, league_id, year, espn_s2, swid, team_id, dry_run=True, session=None, timeout=20):
        self.league_id = int(league_id)
        self.year = int(year)
        self.team_id = int(team_id)
        self.member_id = normalize_swid(swid)
        self.cookies = {"espn_s2": espn_s2, "SWID": self.member_id}
        self.dry_run = dry_run
        self.session = session or requests
        self.timeout = timeout
        self.url = write_url(year, league_id)

    def post(self, payload):
        if self.dry_run:
            logger.info("DRY RUN %s: %s", payload.get("type"), json.dumps(payload))
            return WriteResult(ok=True, status=None, code=None, message="dry run, nothing sent",
                               payload=payload, dry_run=True)
        response = self.session.post(self.url, json=payload, headers=HEADERS,
                                     cookies=self.cookies, timeout=self.timeout)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        ok, code, message, transaction_id = interpret(response.status_code, body)
        result = WriteResult(ok=ok, status=response.status_code, code=code, message=message,
                             payload=payload, body=body, transaction_id=transaction_id)
        level = logging.INFO if ok else logging.WARNING
        logger.log(level, "ESPN write %s", result.summary())
        return result

    # Convenience wrappers so callers never assemble envelopes by hand.

    def lineup(self, scoring_period, moves):
        return self.post(lineup_payload(self.team_id, self.member_id, scoring_period, moves))

    def add_drop(self, scoring_period, add_id=None, drop_ids=(), waiver=False, bid_amount=None, to_slot=None):
        return self.post(add_drop_payload(self.team_id, self.member_id, scoring_period, add_id,
                                          drop_ids, waiver, bid_amount, to_slot))

    def cancel(self, scoring_period, related_id, kind="WAIVER"):
        return self.post(cancel_payload(self.team_id, self.member_id, scoring_period, related_id, kind))

    def propose_trade(self, scoring_period, other_team_id, gives, gets):
        return self.post(trade_propose_payload(self.team_id, self.member_id, scoring_period,
                                               other_team_id, gives, gets))

    def respond_trade(self, scoring_period, related_id, accept, drop_ids=()):
        return self.post(trade_respond_payload(self.team_id, self.member_id, scoring_period,
                                               related_id, accept, drop_ids))

    def withdraw_trade(self, scoring_period, related_id):
        return self.post(trade_withdraw_payload(self.team_id, self.member_id, scoring_period, related_id))
