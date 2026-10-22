"""
The negotiation ledger: one thread per other manager, read at the start of
every general manager run and updated at its end. The service appends the
events it can see on its own (an offer sent, declined, accepted, received);
these tools carry the plan: what we want from him, what we have offered,
what he has asked for, and what happens next.
"""
import json

from claude_agent_sdk import ToolAnnotations, tool

from agent import store
from agent.tools.common import err, text

NAMES = ["read_negotiations", "update_negotiation"]
READ_ONLY = ToolAnnotations(readOnlyHint=True)


def render(rows, team_name):
    if not rows:
        return "no negotiation threads yet"
    out = []
    for r in rows:
        head = (f"## {team_name(r['team_id'])} (team {r['team_id']}) [{r['status']}] updated {r['updated_at'][:16]}\n"
                f"target: {r.get('target') or '-'}\noffered: {r.get('offered') or '-'}\n"
                f"he asks: {r.get('their_asks') or '-'}\nnext: {r.get('next_action') or '-'}"
                f"{' by ' + r['next_at'] if r.get('next_at') else ''}")
        events = "\n".join(f"- {h['at'][:16]} {h['event']}" for h in r["history"][-8:]) or "- (no events)"
        out.append(head + "\n" + events)
    return "\n\n".join(out)


def build(ctx, run):
    @tool("read_negotiations",
          "Every negotiation thread with another manager: status, what we want from him, what we have "
          "offered, what he has asked for, the next action and when, and the dated events (offers sent, "
          "declined, accepted, received). status: open, paused, closed, or omit for all.",
          {"status": str}, READ_ONLY)
    async def read_negotiations(args):
        rows = store.list_negotiations(args.get("status") or None)
        return text(render(rows, ctx.team_name))

    @tool("update_negotiation",
          "Record the plan for one manager's thread. team_id required; the rest optional and only the fields "
          "given change. status: open, paused, or closed. target: what we want from him. offered: what we have "
          "put on the table. their_asks: what he has asked for or hinted at. next_action and next_at (ISO "
          "date): what to do next and when. note: a dated event line to append.",
          {"team_id": int, "status": str, "target": str, "offered": str, "their_asks": str,
           "next_action": str, "next_at": str, "note": str})
    async def update_negotiation(args):
        try:
            team_id = int(args["team_id"])
        except (KeyError, TypeError, ValueError):
            return err("team_id is required")
        if team_id == ctx.cfg.team_id or ctx.team(team_id) is None:
            return err(f"no other team with id {team_id}")
        status = args.get("status")
        if status and status not in ("open", "paused", "closed"):
            return err("status must be open, paused, or closed")
        row = store.upsert_negotiation(team_id, status=status, target=args.get("target"), offered=args.get("offered"),
                                       their_asks=args.get("their_asks"), next_action=args.get("next_action"),
                                       next_at=args.get("next_at"))
        if args.get("note"):
            store.add_negotiation_event(team_id, f"{run.job}: {args['note']}")
            row = store.get_negotiation(team_id)
        return text("updated:\n" + render([row], ctx.team_name))

    return [read_negotiations, update_negotiation]
