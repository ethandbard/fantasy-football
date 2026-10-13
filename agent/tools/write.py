"""
Write tools: every roster action as a preview_* / execute_* pair.

preview_* builds the exact ESPN payload, checks legality against a fresh
roster read, classifies the move with agent.policy, and returns a one-time
token. execute_* refuses anything but a live token, re-reads the roster,
re-validates, and then either posts (auto), queues an ask for Ethan (ask), or
refuses (never). Under full autonomy (the default) nearly everything is auto;
see agent.policy. Every execution is written to the audit log.
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
    "preview_activate", "execute_activate",
    "preview_trade", "execute_trade", "preview_trade_response", "execute_trade_response", "withdraw_trade",
]

TOKEN_TTL = timedelta(minutes=30)
UNDROPPABLE_CODE = "TRAN_ROSTER_PLAYER_NOT_DROPPABLE"


def _item_keys(payload):
    return {(i.get("type"), i.get("playerId")) for i in (payload or {}).get("items", [])}


def roster_full(entries):
    """True when an add needs a drop."""
    return roster.open_spots(entries) <= 0


def retry_of(failed_rows, payload):
    """
    The earlier rejected write today that this payload repeats, or None. A
    rejection blocks the same move for the day, not every move of its kind:
    one undroppable player must not freeze every other waiver claim.
    """
    want = _item_keys(payload)
    for row in failed_rows:
        earlier = row.get("payload")
        earlier = json.loads(earlier) if isinstance(earlier, str) else (earlier or {})
        if _item_keys(earlier) == want:
            return row
    return None
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


def plan_activate(entries, player_id, to_slot_id, drop_ids, slot_counts, extra_moves=()):
    """Plan moving a player out of the IR slot.

    Pure function (no I/O) so it can be unit tested. `entries` are
    roster.RosterEntry objects; `slot_counts` maps starting slot id to count;
    `extra_moves` is a list of (player_id, to_slot_id) applied alongside, e.g.
    benching the starter who currently holds the target slot.

    Returns (plan, problems). plan is a dict with the player entry, target
    slot, the drops needed (empty when the roster has room), the extra moves,
    and a move description. problems is a list of plain-language blockers;
    empty means the plan is good.

    Why this exists: ESPN rejects a lone IR->active move when the roster is
    full (TRAN_ROSTER_LIMIT_EXCEEDED_ONE), and a healthy player sitting in IR
    can also block other transactions. The safe sequence is: drop first (only
    when full), then move — which is what the activate tools execute.
    """
    by_id = {}
    for e in entries:
        try:
            by_id[int(e.player_id)] = e
        except (TypeError, ValueError):
            continue
    try:
        player_id = int(player_id)
    except (TypeError, ValueError):
        return None, ["unknown player"]
    entry = by_id.get(player_id)
    if entry is None:
        return None, [f"player {player_id} is not on my roster"]
    if int(entry.slot_id) != roster.IR_SLOT:
        return None, [f"{entry.name} is not in the IR slot (currently {entry.slot}); "
                       "use preview_lineup for normal lineup moves"]
    if to_slot_id is None:
        return None, ["unknown target slot"]
    to_slot_id = int(to_slot_id)
    if to_slot_id == roster.IR_SLOT:
        return None, ["the target slot cannot be IR"]
    if to_slot_id not in [int(s) for s in (entry.eligible_slot_ids or [])]:
        return None, [f"{entry.name} is not eligible for {roster.slot_name(to_slot_id)}"]

    drop_ids = [int(d) for d in (drop_ids or [])]
    if player_id in drop_ids:
        return None, ["cannot drop the player being activated"]
    need_drop = roster.open_spots(entries) <= 0
    if need_drop and not drop_ids:
        return None, ["roster is full; pass drop_player_ids with a bench player to drop first"]
    drops = []
    for did in drop_ids:
        d = by_id.get(did)
        if d is None:
            return None, [f"player {did} is not on my roster"]
        if d.roster_locked:
            return None, [f"{d.name} is roster-locked right now"]
        drops.append(d)

    moves = []
    for m_pid, m_slot in (extra_moves or ()):
        try:
            m_pid, m_slot = int(m_pid), int(m_slot)
        except (TypeError, ValueError):
            return None, [f"bad extra move {m_pid}->{m_slot}"]
        if m_pid == player_id or m_pid in drop_ids:
            return None, ["an extra move cannot touch the activated player or a dropped player"]
        m_entry = by_id.get(m_pid)
        if m_entry is None:
            return None, [f"player {m_pid} is not on my roster"]
        if m_slot not in [int(s) for s in (m_entry.eligible_slot_ids or [])]:
            return None, [f"{m_entry.name} is not eligible for {roster.slot_name(m_slot)}"]
        if int(m_entry.slot_id) == m_slot:
            continue
        moves.append((m_entry, m_slot))

    # The move must not introduce NEW lineup problems. Problems that already
    # exist (like this player sitting in IR while healthy) don't block it.
    drop_set = {int(d.player_id) for d in drops}
    simulated = []
    for e in entries:
        if int(e.player_id) in drop_set:
            continue
        if int(e.player_id) == player_id:
            moved = dict(e.__dict__)
            moved["slot_id"] = to_slot_id
            moved["slot"] = roster.slot_name(to_slot_id)
            simulated.append(roster.RosterEntry(**moved))
            continue
        replaced = False
        for m_entry, m_slot in moves:
            if int(e.player_id) == int(m_entry.player_id):
                moved = dict(e.__dict__)
                moved["slot_id"] = m_slot
                moved["slot"] = roster.slot_name(m_slot)
                simulated.append(roster.RosterEntry(**moved))
                replaced = True
                break
        if not replaced:
            simulated.append(e)
    before = set(roster.lineup_problems(entries, slot_counts or {}))
    new_problems = [p for p in roster.lineup_problems(simulated, slot_counts or {}) if p not in before]
    if new_problems:
        return None, ["resulting lineup would be illegal: " + "; ".join(new_problems)]
    move_desc = f"{entry.name} {entry.slot}->{roster.slot_name(to_slot_id)}"
    for m_entry, m_slot in moves:
        move_desc += f", {m_entry.name} {m_entry.slot}->{roster.slot_name(m_slot)}"
    return {"entry": entry, "to_slot_id": to_slot_id,
            "drop_ids": [int(d.player_id) for d in drops], "drop_entries": drops,
            "need_drop": need_drop, "moves": moves, "move_desc": move_desc}, []


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

    def blocked_today(kind, payload):
        row = retry_of(store.failed_writes_today(kind), payload)
        if row:
            return f"this same {kind} write was rejected earlier today ({row['code'] or row['message']}); no retry until tomorrow"
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
        block = blocked_today(preview.kind, preview.payload)
        if block:
            return err(block)
        result = ctx.writer.post(preview.payload)
        store.log_transaction(preview.kind, preview.payload, result, description=preview.description,
                              reason=reason, run_id=run.run_id)
        run.transactions.append({"kind": preview.kind, "description": preview.description, "result": result.summary()})
        ctx.rosters(refresh=True)
        if result.ok:
            return text(f"EXECUTED {preview.description}. {result.summary()}")
        if result.code == UNDROPPABLE_CODE:
            # ESPN's player data said droppable; its transaction endpoint disagreed. Trust the endpoint from now on.
            names = []
            for item in preview.payload.get("items", []):
                if item.get("type") == "DROP":
                    store.remember_undroppable(item["playerId"])
                    e = ctx.entry(item["playerId"])
                    names.append(e.name if e else str(item["playerId"]))
            return err(f"ESPN rejected {preview.description}: {', '.join(names)} is on ESPN's undroppable list. "
                       "That player is now never-tier for drops; pick a different drop. Other writes are unaffected.")
        return err(f"ESPN rejected {preview.description}. {result.summary()}. Do not retry that move today.")

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
          "including the one going to the bench. Returns a token for execute_lineup. "
          "To bring a healthy player OUT of IR when the roster is full, use preview_activate "
          "instead — ESPN rejects that as a lone lineup move.",
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
        if add_id is not None and not drops and roster_full(entries):
            return None, "roster is full; include a drop"
        desc = ", ".join(filter(None, [add_desc] + [f"drop {e.name}" for e in drops]))
        payload = writes.add_drop_payload(cfg.team_id, cfg.swid, ctx.scoring_period, add_id,
                                          [e.player_id for e in drops], waiver=waiver,
                                          bid_amount=0 if waiver else None)
        learned = store.undroppable_ids()
        drop_dicts = [dict(e.to_dict(), droppable=e.droppable and e.player_id not in learned) for e in drops]
        decision = policy.classify_add_drop(adds, drop_dicts, [e.to_dict() for e in entries], ctx.rules)
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
        if (not policy.full_autonomy(ctx.rules) and preview.extra.get("add_id") is not None
                and run.adds_executed >= ctx.rules.get("max_adds_per_run", 3)):
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

    # ---------------------------------------------------------- IR activation

    def build_activate_preview(player_id, to_slot, drop_ids, extra_moves):
        """Validate an IR activation against a fresh roster.

        Shared by preview_activate and by execute_activate's recheck, mirroring
        how execute_lineup re-validates. Returns (built, problem).
        """
        entries = ctx.my_roster(refresh=True)
        plan, problems = plan_activate(
            entries, player_id, slot_id_from(to_slot),
            [int(x) for x in (drop_ids or [])], ctx.slot_counts(),
            [(int(m.get("player_id")), slot_id_from(m.get("to_slot")))
             for m in (extra_moves or [])])
        if problems:
            return None, "; ".join(problems)
        drop_payload, drop_desc, decision = None, "", None
        if plan["need_drop"]:
            drop_payload = writes.add_drop_payload(cfg.team_id, cfg.swid, ctx.scoring_period,
                                                   None, plan["drop_ids"], waiver=False)
            drop_desc = "drop " + ", ".join(e.name for e in plan["drop_entries"])
            learned = store.undroppable_ids()
            drop_dicts = [dict(e.to_dict(), droppable=e.droppable and e.player_id not in learned)
                          for e in plan["drop_entries"]]
            decision = policy.settle(policy.classify_add_drop([], drop_dicts,
                                      [e.to_dict() for e in entries], ctx.rules), ctx.rules)
            if decision.tier != policy.AUTO:
                return None, (f"the drop step is {decision.tier} ({decision}); this tool only runs "
                               "automatic moves — use preview_add_drop to queue it for approval instead")
        move_items = [{"player_id": int(plan["entry"].player_id),
                       "from_slot": roster.IR_SLOT, "to_slot": plan["to_slot_id"]}]
        for m_entry, m_slot in plan["moves"]:
            move_items.append({"player_id": int(m_entry.player_id),
                               "from_slot": int(m_entry.slot_id), "to_slot": m_slot})
        move_payload = writes.lineup_payload(cfg.team_id, cfg.swid, ctx.scoring_period, move_items)
        desc = plan["move_desc"]
        if plan["need_drop"]:
            desc += f" (drops {', '.join(e.name for e in plan['drop_entries'])} first; roster is full)"
        if decision is None:
            decision = policy.Decision(policy.AUTO, ["lineup changes are always auto"])
        return {"plan": plan, "drop_payload": drop_payload, "drop_desc": drop_desc,
                "move_payload": move_payload, "description": desc, "decision": decision,
                "player_id": int(plan["entry"].player_id), "to_slot": to_slot,
                "drop_ids": plan["drop_ids"],
                "extra_moves": [{"player_id": int(m[0].player_id), "to_slot": m[1]}
                                for m in plan["moves"]]}, None

    @tool("preview_activate",
          "Move a player OUT of the IR slot to an active slot (use when they are healthy again). "
          "When the roster is full, pass drop_player_ids: this drops them first, then moves the "
          "player, under one token. extra_moves optionally benches whoever holds the target slot, "
          "as {player_id, to_slot} pairs. Use this instead of preview_lineup for IR activations on "
          "a full roster — ESPN rejects a lone IR->active move.",
          {"player_id": int, "to_slot": str, "drop_player_ids": list, "extra_moves": list})
    async def preview_activate(args):
        if args.get("player_id") is None:
            return err("give the player_id of the player in the IR slot")
        built, problem = build_activate_preview(args.get("player_id"), args.get("to_slot"),
                                                args.get("drop_player_ids"), args.get("extra_moves"))
        if problem:
            return err(problem)
        preview = Preview("activate", built["move_payload"], built["description"], built["decision"],
                          {"player_id": built["player_id"], "to_slot": built["to_slot"],
                           "drop_ids": built["drop_ids"], "extra_moves": built["extra_moves"],
                           "drop_payload": built["drop_payload"], "drop_desc": built["drop_desc"]})
        previews[preview.token] = preview
        lines = [f"PREVIEW activate: {built['description']}",
                 f"Permission: {built['decision']}",
                 f"Token: {preview.token} (expires in 30 minutes)"]
        if built["drop_payload"] is not None:
            dropped = {k: v for k, v in built["drop_payload"].items() if k != "memberId"}
            lines.append("Step 1 posts first: " + json.dumps(dropped))
        moved = {k: v for k, v in built["move_payload"].items() if k != "memberId"}
        lines.append("Step 2 posts next: " + json.dumps(moved))
        return text("\n".join(lines))

    @tool("execute_activate", "Execute a previewed IR activation by token. reason: one line for the log.",
          {"token": str, "reason": str})
    async def execute_activate(args):
        preview, problem = take(args.get("token"))
        if problem:
            return problem
        reason = args.get("reason")
        extra = preview.extra
        # Re-validate against a fresh roster, like execute_lineup does.
        built, problem = build_activate_preview(extra["player_id"], extra["to_slot"],
                                                extra["drop_ids"], extra["extra_moves"])
        if problem:
            return err(f"roster changed since preview: {problem}")
        drop_desc = built["drop_desc"]
        if built["plan"]["need_drop"]:
            block = blocked_today("drop", built["drop_payload"])
            if block:
                return err(block)
            result = ctx.writer.post(built["drop_payload"])
            store.log_transaction("drop", built["drop_payload"], result,
                                  description=drop_desc, reason=reason, run_id=run.run_id)
            run.transactions.append({"kind": "drop", "description": drop_desc,
                                     "result": result.summary()})
            ctx.rosters(refresh=True)
            if not result.ok:
                return err(f"ESPN rejected {drop_desc}: {result.summary()}. "
                           "Activation aborted; the IR player stays put.")
            # Rebuild the move against the post-drop roster.
            rebuilt, problem = build_activate_preview(extra["player_id"], extra["to_slot"], [], [])
            if problem:
                return err(f"Dropped {drop_desc}, but the activation move now fails: {problem}. "
                           "The roster is still legal; finish the move by hand with preview_lineup.")
            built = rebuilt
        # No blocked_today check on the move: an earlier same-day rejection of this exact move
        # failed on a full roster, and the drop above just changed that precondition. The fresh
        # legality check above is the guard now.
        result = ctx.writer.post(built["move_payload"])
        store.log_transaction("lineup", built["move_payload"], result,
                              description=built["description"], reason=reason, run_id=run.run_id)
        run.transactions.append({"kind": "lineup", "description": built["description"],
                                 "result": result.summary()})
        ctx.rosters(refresh=True)
        if not result.ok:
            return err(f"ESPN rejected {built['description']}: {result.summary()}. "
                       "Do not retry that move today.")
        return text(f"EXECUTED {built['description']}. {result.summary()}")

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
        if not policy.full_autonomy(ctx.rules) and len(pending) >= ctx.rules.get("max_open_proposals", 3):
            return err(f"already {len(pending)} open proposals; the cap is {ctx.rules.get('max_open_proposals', 3)}")
        payload = writes.trade_propose_payload(cfg.team_id, cfg.swid, ctx.scoring_period, other,
                                               [e.player_id for e in gives], [e.player_id for e in gets])
        desc = f"offer {', '.join(e.name for e in gives)} to {ctx.team_name(other)} for {', '.join(e.name for e in gets)}"
        decision = policy.classify_trade_propose([e.to_dict() for e in gives], [e.to_dict() for e in gets],
                                                 [e.to_dict() for e in ctx.my_roster()], ctx.rules)
        preview = Preview("trade_propose", payload, desc, decision)
        previews[preview.token] = preview
        return text(preview.to_text())

    @tool("execute_trade", "Send a previewed trade proposal by token. The preview's permission line says whether it "
          "sends now or queues for approval. reason: the case for it.",
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

    @tool("execute_trade_response", "Execute a previewed accept or decline by token. The preview's permission line "
          "says whether it executes now or queues for approval.",
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
            preview_activate, execute_activate,
            preview_trade, execute_trade, preview_trade_response, execute_trade_response, withdraw_trade]
