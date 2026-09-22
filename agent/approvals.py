"""
Executes an approved ask without a model in the loop.

The payload was fully built and classified at preview time. Before posting,
the roster is re-read and the payload checked against it, because a day may
have passed: a player may have been dropped, locked, or traded since.
"""
import json
import logging

import gamedaybot.espn.roster as roster
from agent import ledger, store
from agent.espn_ctx import EspnContext

logger = logging.getLogger(__name__)


def _still_valid(ctx, kind, payload):
    entries = ctx.my_roster(refresh=True)
    by_id = {e.player_id: e for e in entries}
    for item in payload.get("items", []):
        t = item.get("type")
        if t == "LINEUP":
            e = by_id.get(item["playerId"])
            if e is None:
                return f"player {item['playerId']} is no longer on the roster"
            if e.slot_id != item["fromLineupSlotId"]:
                return f"{e.name} is no longer in the slot the move assumed"
            if e.lineup_locked:
                return f"{e.name} is locked"
        elif t == "DROP":
            e = by_id.get(item["playerId"])
            if e is None:
                return f"player {item['playerId']} is no longer on the roster"
        elif t == "TRADE" and item.get("fromTeamId") == ctx.cfg.team_id:
            if by_id.get(item["playerId"]) is None:
                return f"player {item['playerId']} is no longer on the roster"
    if payload.get("scoringPeriodId") != ctx.scoring_period:
        return "the scoring period has rolled since this was proposed"
    return None


def execute_ask(cfg, rules, ask):
    """Returns (ok, message). Marks the ask executed or failed."""
    ctx = EspnContext(cfg, rules, write_enabled=True)
    payload = json.loads(ask["payload"]) if isinstance(ask["payload"], str) else ask["payload"]
    why = _still_valid(ctx, ask["kind"], payload)
    if why:
        store.resolve_ask(ask["id"], "failed", f"stale: {why}")
        ledger.record_ask(cfg, ask, "failed", f"stale: {why}")
        return False, f"could not execute ask {ask['id']}: {why}"
    result = ctx.writer.post(payload)
    store.log_transaction(ask["kind"], payload, result, description=ask["description"],
                          reason=f"approved ask {ask['id']}", run_id=ask.get("run_id"))
    if result.ok:
        store.resolve_ask(ask["id"], "executed", result.summary())
        ledger.record_ask(cfg, ask, "executed", result.summary())
        return True, f"executed ask {ask['id']}: {ask['description']} ({result.summary()})"
    store.resolve_ask(ask["id"], "failed", result.summary())
    ledger.record_ask(cfg, ask, "failed", result.summary())
    return False, f"ESPN rejected ask {ask['id']}: {result.summary()}"
