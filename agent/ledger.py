"""
The system's own record of what became of the agents' actions, written
with no model in the loop.

A brief is a snapshot. "Queued as an ask, needs your approval" is true when
the trade reviewer writes it and stale the moment the owner reacts. The
owner's reaction, ESPN's answer, and the other manager's decline all happen
while no agent is running, so nothing written by a model can capture them.
This module records them instead: an entry in the season log (which the
weekly jobs read), an outcome on the transaction row (which the
get_agent_activity tool reports), and, where it is news, a line in Discord.
"""
import json
import logging
from datetime import datetime, timezone

import gamedaybot.espn.roster as roster
from agent import discord_out, store

logger = logging.getLogger(__name__)

ASK_VERBS = {
    "executed": "was approved and sent to ESPN",
    "failed": "was approved but could not be sent",
    "rejected": "was rejected by the owner",
    "expired": "expired unanswered",
}


def season_log_append(data_dir, source, markdown):
    """Append one dated entry. The append_season_log tool and the system share this format."""
    path = data_dir / "season-log.md"
    stamp = datetime.now(timezone.utc).astimezone(roster._eastern()).strftime("%Y-%m-%d %I:%M %p ET")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n\n### {stamp} · {source}\n\n{markdown.strip()}\n")


def _log(cfg, source, line):
    try:
        season_log_append(cfg.data_dir, source, line)
    except OSError:
        logger.exception("could not write the season log")


def record_ask(cfg, ask, status, detail=None, announce=False):
    """
    An ask left the pending state. `status` is executed, failed, rejected,
    or expired. Approvals and rejections already echo in Discord through the
    bot, so only pass announce=True for the silent case, expiry.
    """
    verb = ASK_VERBS.get(status, status)
    line = f"Ask {ask['id']} ({ask['description']}) {verb}" + (f": {detail}" if detail else ".")
    _log(cfg, "approvals", line)
    if announce:
        discord_out.line(cfg, line, kind="info")
    return line


def check_proposals(cfg, ctx):
    """
    What became of trade proposals the agent sent. ESPN lists an open
    proposal as pending, then drops it with no record of why. If a player
    the agent gave is gone from its roster, the trade went through;
    otherwise the other side declined it, or it expired. While a proposal
    is still open its expiry and acceptance are remembered, so the reason
    can be told apart once it disappears. Returns [(transaction_id,
    outcome)] for the outcomes recorded on this call.
    """
    open_rows = store.unresolved_proposals()
    if not open_rows:
        return []
    pending = {t["id"]: t for t in ctx.pending_transactions()}
    outcomes = []
    mine = None
    for row in open_rows:
        tid = row["transaction_id"]
        key = f"proposal:{tid}"
        live = pending.get(tid)
        if live is not None:
            store.set_note(key, json.dumps({"expires": live.get("expires"), "accepted": bool(live.get("accepted"))}))
            continue
        seen = json.loads(store.get_note(key) or "{}")
        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else (row["payload"] or {})
        gave = [int(i["playerId"]) for i in payload.get("items", [])
                if i.get("fromTeamId") == cfg.team_id and i.get("playerId") is not None]
        if mine is None:
            mine = {e.player_id for e in ctx.my_roster(refresh=True)}
        if gave and all(p not in mine for p in gave):
            outcome, what = "accepted", "went through"
        elif seen.get("accepted"):
            outcome, what = "reversed", "was accepted but did not go through (vetoed or reversed in review)"
        else:
            expires = seen.get("expires")
            now = datetime.now(timezone.utc).isoformat(timespec="minutes")
            if expires and now >= expires:
                outcome, what = "expired", "expired unanswered"
            else:
                outcome, what = "declined", "was declined by the other side"
        store.set_transaction_outcome(row["id"], outcome)
        line = f"Trade proposal {tid[:8]} {what}: {row.get('description') or 'no description'}."
        _log(cfg, "trades", line)
        discord_out.line(cfg, line, kind="info")
        outcomes.append((tid, outcome))
    return outcomes
