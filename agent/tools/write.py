"""
Write tools: every roster action as a preview_* / execute_* pair.

preview_* builds the exact ESPN payload, checks legality against a fresh
roster read, classifies the move with agent.policy, and returns a one-time
token. execute_* refuses anything but a live token, re-reads the roster,
re-validates, and then either posts (auto), queues an ask for Ethan (ask), or
refuses (never). Every execution is written to the audit log.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from claude_agent_sdk import tool

import gamedaybot.espn.roster as roster
import gamedaybot.espn.writes as writes
from agent import policy, store
from agent.tools.common import err, text

NAMES = [
    "preview_lineup", "execute_lineup", "preview_add_drop", "execute_add_drop", "cancel_waiver",
    "preview_trade", "execute_trade", "preview_trade_response", "execute_trade_response", "withdraw_trade",
]

TOKEN_TTL = timedelta(minutes=30)
SLOT_ALIASES = {"FLEX": 23, "BENCH": 20, "BN": 20, "BE": 20, "IR": 21, "DST": 16, "DEF": 16}


def slot_id_from(value):
    if isinstance(value, int):
        return value
    s = str(value).strip().upper()
    if s.isdigit():
        return int(s)
    if s in SLOT_ALIASES:
        return SLOT_ALIASES[s]
    return roster.SLOT_ID_BY_NAME.get(s)


class Preview:
    def __init__(self, kind, payload, description, decision, extra=None):
        self.token = uuid.uuid4().hex[:10]
        self.kind = kind
        self.payload = payload
        self.description = description
        self.decision = decision
        self.extra = extra or {}
        self.expires = datetime.now(timezone.utc) + TOKEN_TTL

    def to_text(self):
        shown = {k: v for k, v in self.payload.items() if k != "memberId"}
        body = [f"PREVIEW {self.kind}: {self.description}",
                f"Permission: {self.decision}",
                f"Token: {self.token} (expires in 30 minutes)",
                "Payload: " + json.dumps(shown)]
        if self.decision.tier == policy.NEVER:
            body.append("This move will be refused if executed.")
        elif self.decision.tier == policy.ASK:
            body.append("Executing will queue this for Ethan's approval in Discord rather than post it.")
        return "\n".join(body)


def build(ctx, run):
    cfg = ctx.cfg
    previews = {}

    def take(token):
        p = previews.pop(str(token), None)
        if p is None:
            return None, err("unknown or already-used token; preview again")
        if datetime.now(timezone.utc) > p.expires:
            return None, err("token expired; preview again")
        return p, None

    def chain_target(preview):
        """
        A pending waiver ask from this run that shares this claim's drop, if
        any. Fallback claims ride on the primary's ask so one approval covers
        them; a second separate ask would expire or wait on its own.
        """
        if preview.kind != "waiver" or preview.extra.get("add_id") is None or not preview.extra.get("drop_ids"):
            return None
        drops = set(preview.extra["drop_ids"])
        for ask_id in run.asks:
            ask = store.get_ask(ask_id)
            if not ask or ask["status"] != "pending" or ask["kind"] != "waiver":
                continue
            payload = json.loads(ask["payload"]) if isinstance(ask["payload"], str) else ask["payload"]
            first = payload["chain"][0] if "chain" in payload else payload
            if {i["playerId"] for i in first.get("items", []) if i.get("type") == "DROP"} == drops:
                return ask
        return None

    def blocked_today(kind):
        failed = store.failed_writes_today(kind)
        if failed:
            last = failed[-1]
            return f"a {kind} write was rejected earlier today ({last['code'] or last['message']}); no retry until tomorrow"
        return None

    def finish(preview, reason):
        """Post, queue, or refuse according to the decision. Returns a tool result."""
        if preview.decision.tier == policy.NEVER:
            return err(f"refused: {preview.decision}")
        if preview.decision.tier == policy.ASK:
            parent = chain_target(preview)
            if parent is not None:
                store.append_to_ask(parent["id"], preview.payload, preview.description)
                return text(f"added to ask {parent['id']} as a fallback claim sharing the same drop; one approval "
                            "covers the whole chain, and ESPN skips any claim whose drop is already gone. "
                            "Say so in the brief.")
            ask_id = store.create_ask(preview.kind, preview.description, preview.payload,
                                      reason=reason or "; ".join(preview.decision.reasons), run_id=run.run_id,
                                      expiry_hours=ctx.rules.get("ask_expiry_hours", 24))
            run.asks.append(ask_id)
            return text(f"queued as ask {ask_id} for Ethan's approval ({preview.decision}). "
                        f"It executes only if he approves within {ctx.rules.get('ask_expiry_hours', 24)} hours. "
                        "Say so in the brief; do not describe it as done.")
        block = blocked_today(preview.kind)
        if block:
            return err(block)
        result = ctx.writer.post(preview.payload)
        store.log_transaction(preview.kind, preview.payload, result, description=preview.description,
                              reason=reason, run_id=run.run_id)
        run.transactions.append({"kind": preview.kind, "description": preview.description, "result": result.summary()})
        ctx.rosters(refresh=True)
        if result.ok:
            return text(f"EXECUTED {preview.description}. {result.summary()}")
        return err(f"ESPN rejected {preview.description}. {result.summary()}. Do not retry today.")

    # ------------------------------------------------------------ lineup

    def build_lineup_preview(moves):
        entries = ctx.my_roster(refresh=True)
        by_id = {e.player_id: e for e in entries}
        items, descs = [], []
        for m in moves:
            e = by_id.get(int(m["player_id"]))
            if e is None:
                return None, f"player {m['player_id']} is not on my roster"
            to_slot = slot_id_from(m["to_slot"])
            if to_slot is None:
                return None, f"unknown slot {m['to_slot']}"
            if e.lineup_locked:
                return None, f"{e.name} is locked"
            if to_slot == e.slot_id:
                continue
            if to_slot not in e.eligible_slot_ids:
                return None, f"{e.name} is not eligible for {roster.slot_name(to_slot)}"
            items.append({"player_id": e.player_id, "from_slot": e.slot_id, "to_slot": to_slot})
            descs.append(f"{e.name} {e.slot}->{roster.slot_name(to_slot)}")
        if not items:
            return None, "no slot actually changes"
        simulated = []
        for e in entries:
            new = dict(e.__dict__)
            for it in items:
                if it["player_id"] == e.player_id:
                    new["slot_id"] = it["to_slot"]
                    new["slot"] = roster.slot_name(it["to_slot"])
            simulated.append(roster.RosterEntry(**new))
        problems = roster.lineup_problems(simulated, ctx.slot_counts())
        if problems:
            return None, "resulting lineup is illegal: " + "; ".join(problems)
        payload = writes.lineup_payload(cfg.team_id, cfg.swid, ctx.scoring_period, items)
        decision = policy.classify_lineup(items, entries, ctx.rules)
        return Preview("lineup", payload, ", ".join(descs), decision, {"items": items}), None

    @tool("preview_lineup",
          "Preview lineup moves. moves: list of {player_id, to_slot} where to_slot is a slot name "
          "(QB, RB, WR, TE, FLEX, D/ST, K, BE, IR) or id. Include every player whose slot changes, "
          "including the one going to the bench. Returns a token for execute_lineup.",
          {"moves": list})
    async def preview_lineup(args):
        preview, problem = build_lineup_preview(args.get("moves") or [])
        if problem:
            return err(problem)
        previews[preview.token] = preview
        return text(preview.to_text())

    @tool("execute_lineup", "Execute a previewed lineup change by token. reason: one line for the log.",
          {"token": str, "reason": str})
    async def execute_lineup(args):
        preview, problem = take(args.get("token"))
        if problem:
            return problem
        fresh, why = build_lineup_preview([{"player_id": i["player_id"], "to_slot": i["to_slot"]} for i in preview.extra["items"]])
        if why:
            return err(f"roster changed since preview: {why}")
        fresh.decision = preview.decision
        return finish(fresh, args.get("reason"))

    # ---------------------------------------------------------- add/drop

    def build_add_drop_preview(add_id, drop_ids, waiver):
        entries = ctx.my_roster(refresh=True)
        by_id = {e.player_id: e for e in entries}
        drops = []
        for pid in drop_ids:
            e = by_id.get(int(pid))
            if e is None:
                return None, f"player {pid} is not on my roster"
            if e.roster_locked:
                return None, f"{e.name} is roster-locked right now"
            drops.append(e)
        adds = []
        add_desc = ""
        if add_id is not None:
            pool = {d["player_id"]: d for d in ctx.free_agents(size=400)}
            fa = pool.get(int(add_id))
            if fa is None:
                return None, f"player {add_id} is not a free agent or on waivers"
            if fa["status"] == "WAIVERS" and not waiver:
                return None, f"{fa['name']} is on waivers; preview again with waiver=true"
            adds.append(fa)
            add_desc = f"add {fa['name']} ({fa['position']} {fa['pro_team']})" + (" via waiver claim" if waiver else "")
        slots = len(entries) - len(drops) + len(adds)
        limit = len(entries) if add_id is None else len(entries)
        if add_id is not None and not drops and len(entries) >= 16:
            return None, "roster is full; include a drop"
        desc = ", ".join(filter(None, [add_desc] + [f"drop {e.name}" for e in drops]))
        payload = writes.add_drop_payload(cfg.team_id, cfg.swid, ctx.scoring_period, add_id,
                                          [e.player_id for e in drops], waiver=waiver,
                                          bid_amount=0 if waiver else None)
        decision = policy.classify_add_drop(adds, [e.to_dict() for e in drops], [e.to_dict() for e in entries], ctx.rules)
        kind = "waiver" if waiver else ("add_drop" if add_id is not None else "drop")
        return Preview(kind, payload, desc, decision, {"add_id": add_id, "drop_ids": [e.player_id for e in drops], "waiver": waiver}), None

    @tool("preview_add_drop",
          "Preview a free-agent add, a waiver claim, a drop, or an add with drops. add_player_id may be omitted "
          "for a plain drop. waiver=true submits a claim for a player on waivers.",
          {"add_player_id": int, "drop_player_ids": list, "waiver": bool})
    async def preview_add_drop(args):
        add_id = args.get("add_player_id")
        drop_ids = [int(x) for x in (args.get("drop_player_ids") or [])]
        if add_id is None and not drop_ids:
            return err("give an add_player_id, drop_player_ids, or both")
        preview, problem = build_add_drop_preview(add_id, drop_ids, bool(args.get("waiver")))
        if problem:
            return err(problem)
        previews[preview.token] = preview
        return text(preview.to_text())

    @tool("execute_add_drop", "Execute a previewed add/drop or waiver claim by token. reason: one line for the log.",
          {"token": str, "reason": str})
    async def execute_add_drop(args):
        preview, problem = take(args.get("token"))
        if problem:
            return problem
        if run.adds_executed >= ctx.rules.get("max_adds_per_run", 3) and preview.extra.get("add_id") is not None:
            return err(f"this run already executed {run.adds_executed} adds; the cap is {ctx.rules.get('max_adds_per_run', 3)}")
        fresh, why = build_add_drop_preview(preview.extra["add_id"], preview.extra["drop_ids"], preview.extra["waiver"])
        if why:
            return err(f"roster changed since preview: {why}")
        result = finish(fresh, args.get("reason"))
        if not result.get("is_error") and preview.extra.get("add_id") is not None and fresh.decision.tier == policy.AUTO:
            run.adds_executed += 1
        return result

    @tool("cancel_waiver", "Cancel one of my pending waiver claims by its transaction id (see get_pending_transactions).",
          {"transaction_id": str, "reason": str})
    async def cancel_waiver(args):
        mine = [t for t in ctx.pending_transactions() if t["team_id"] == cfg.team_id and t["type"] == "WAIVER"
                and str(t["id"]) == str(args["transaction_id"])]
        if not mine:
            return err("no pending waiver claim of mine with that id")
        payload = writes.cancel_payload(cfg.team_id, cfg.swid, ctx.scoring_period, args["transaction_id"], kind="WAIVER")
        preview = Preview("cancel_waiver", payload, f"cancel waiver claim {args['transaction_id']}", policy.classify_cancel_waiver(ctx.rules))
        return finish(preview, args.get("reason"))

    # ------------------------------------------------------------- trades

    def resolve_players(team_id, ids):
        out = []
        for pid in ids:
            e = ctx.entry(pid, team_id=team_id)
            if e is None:
                return None, f"player {pid} is not on {ctx.team_name(team_id)}"
            out.append(e)
        return out, None

    @tool("preview_trade",
          "Preview a trade proposal to another team. give_player_ids are mine, get_player_ids are theirs.",
          {"other_team_id": int, "give_player_ids": list, "get_player_ids": list})
    async def preview_trade(args):
        ctx.rosters(refresh=True)
        other = int(args["other_team_id"])
        gives, why = resolve_players(cfg.team_id, [int(x) for x in args.get("give_player_ids") or []])
        if why:
            return err(why)
        gets, why = resolve_players(other, [int(x) for x in args.get("get_player_ids") or []])
        if why:
            return err(why)
        if not gives or not gets:
            return err("a trade needs players on both sides")
        open_ids = store.agent_proposed_trade_ids()
        pending = [t for t in ctx.pending_transactions() if t["type"] == "TRADE_PROPOSAL" and t["status"] == "PENDING"
                   and t["team_id"] == cfg.team_id]
        if len(pending) >= ctx.rules.get("max_open_proposals", 3):
            return err(f"already {len(pending)} open proposals; the cap is {ctx.rules.get('max_open_proposals', 3)}")
        payload = writes.trade_propose_payload(cfg.team_id, cfg.swid, ctx.scoring_period, other,
                                               [e.player_id for e in gives], [e.player_id for e in gets])
        desc = f"offer {', '.join(e.name for e in gives)} to {ctx.team_name(other)} for {', '.join(e.name for e in gets)}"
        decision = policy.classify_trade_propose([e.to_dict() for e in gives], [e.to_dict() for e in gets],
                                                 [e.to_dict() for e in ctx.my_roster()], ctx.rules)
        preview = Preview("trade_propose", payload, desc, decision)
        previews[preview.token] = preview
        return text(preview.to_text())

    @tool("execute_trade", "Send a previewed trade proposal by token (queues for approval). reason: the case for it.",
          {"token": str, "reason": str})
    async def execute_trade(args):
        preview, problem = take(args.get("token"))
        if problem:
            return problem
        return finish(preview, args.get("reason"))

    @tool("preview_trade_response",
          "Preview accepting or declining an incoming offer by its id. drop_player_ids only if accepting overflows the roster.",
          {"offer_id": str, "accept": bool, "drop_player_ids": list})
    async def preview_trade_response(args):
        offers = {str(t["id"]): t for t in ctx.incoming_offers()}
        offer = offers.get(str(args["offer_id"]))
        if offer is None:
            return err("no pending incoming offer with that id")
        accept = bool(args.get("accept"))
        gives = [i for i in offer["items"] if i["from_team_id"] == cfg.team_id]
        gets = [i for i in offer["items"] if i["to_team_id"] == cfg.team_id]
        give_entries = [ctx.entry(i["player_id"]) for i in gives]
        give_entries = [e for e in give_entries if e]
        drops = [int(x) for x in args.get("drop_player_ids") or []] if accept else []
        payload = writes.trade_respond_payload(cfg.team_id, cfg.swid, ctx.scoring_period, offer["id"], accept, drops)
        verb = "accept" if accept else "decline"
        desc = (f"{verb} offer {offer['id']} from {offer['team']}: give {', '.join(i['player'] or '?' for i in gives)}, "
                f"get {', '.join(i['player'] or '?' for i in gets)}")
        decision = policy.classify_trade_respond(accept, [e.to_dict() for e in give_entries], gets,
                                                 [e.to_dict() for e in ctx.my_roster()], ctx.rules)
        preview = Preview("trade_accept" if accept else "trade_decline", payload, desc, decision)
        previews[preview.token] = preview
        return text(preview.to_text())

    @tool("execute_trade_response", "Execute a previewed accept (queues for approval) or decline (auto) by token.",
          {"token": str, "reason": str})
    async def execute_trade_response(args):
        preview, problem = take(args.get("token"))
        if problem:
            return problem
        return finish(preview, args.get("reason"))

    @tool("withdraw_trade", "Withdraw a trade proposal the agent itself sent, by its id.", {"offer_id": str, "reason": str})
    async def withdraw_trade(args):
        decision = policy.classify_withdraw(args["offer_id"], store.agent_proposed_trade_ids(), ctx.rules)
        payload = writes.trade_withdraw_payload(cfg.team_id, cfg.swid, ctx.scoring_period, args["offer_id"])
        preview = Preview("trade_withdraw", payload, f"withdraw proposal {args['offer_id']}", decision)
        return finish(preview, args.get("reason"))

    return [preview_lineup, execute_lineup, preview_add_drop, execute_add_drop, cancel_waiver,
            preview_trade, execute_trade, preview_trade_response, execute_trade_response, withdraw_trade]
