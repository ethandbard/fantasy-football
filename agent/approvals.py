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
    """
    Returns (ok, message). Marks the ask executed or failed. A chained ask
    (a waiver claim with fallbacks sharing its drop) posts each claim in
    order; it is executed when at least one posts.
    """
    ctx = EspnContext(cfg, rules, write_enabled=True)
    payload = json.loads(ask["payload"]) if isinstance(ask["payload"], str) else ask["payload"]
    if isinstance(payload, dict) and "chain" in payload:
        chain = list(payload["chain"])
        descs = list(payload.get("descriptions") or [ask["description"]] * len(chain))
    else:
        chain, descs = [payload], [ask["description"]]
    outcomes = []
    for one, desc in zip(chain, descs):
        why = _still_valid(ctx, ask["kind"], one)
        if why:
            outcomes.append((False, f"{desc}: stale, {why}"))
            continue
        result = ctx.writer.post(one)
        store.log_transaction(ask["kind"], one, result, description=desc,
                              reason=f"approved ask {ask['id']}", run_id=ask.get("run_id"))
        outcomes.append((result.ok, f"{desc}: {result.summary()}"))
    summary = "; ".join(text for _, text in outcomes)
    if any(ok for ok, _ in outcomes):
        store.resolve_ask(ask["id"], "executed", summary)
        ledger.record_ask(cfg, ask, "executed", summary)
        return True, f"executed ask {ask['id']}: {summary}"
    store.resolve_ask(ask["id"], "failed", summary)
    ledger.record_ask(cfg, ask, "failed", summary)
    return False, f"could not execute ask {ask['id']}: {summary}"
