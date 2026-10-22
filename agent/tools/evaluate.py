"""
The general manager's arithmetic: playoff odds, trade evaluation, the
market map, and chart lookups. Built on agent.evaluate over a State
assembled once per run from the week's analytics build, the live rosters,
the league schedule, and the cached trade value chart.
"""
import json
import logging
from pathlib import Path

from claude_agent_sdk import ToolAnnotations, tool

import gamedaybot.espn.roster as roster
from agent import chart as chart_mod, evaluate, store
from agent.tools.common import err, text

logger = logging.getLogger(__name__)

NAMES = ["playoff_odds", "evaluate_trade", "market_map", "chart_values"]
READ_ONLY = ToolAnnotations(readOnlyHint=True)


def _analysis(data_dir, week):
    """The newest analytics build at or before `week`, as (week, json) or (None, None)."""
    from agent.analytics import pipeline
    for w in range(int(week), max(int(week) - 3, 0), -1):
        path = pipeline.analytics_dir(data_dir, w) / "analysis.json"
        if path.exists():
            try:
                return w, json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return None, None


def _slot_names(entry):
    return [roster.slot_name(s) for s in entry.eligible_slot_ids if s not in (roster.BENCH_SLOT, roster.IR_SLOT)]


def load_state(ctx, cfg):
    """A State for this run, or (None, why) when no analytics build exists."""
    week = int(ctx.week)
    built_week, body = _analysis(cfg.data_dir, week)
    if body is None:
        return None, "no analytics build on disk; run the analytics job first"
    meta = body.get("meta") or {}
    rows = {int(r["player_id"]): r for r in (body.get("tables") or {}).get("players") or [] if r.get("player_id") is not None}
    lg = ctx.league()
    reg_weeks = int(getattr(lg.settings, "reg_season_count", 14) or 14)
    playoff_teams = int(getattr(lg.settings, "playoff_team_count", 4) or 4)
    slot_counts = {roster.slot_name(s): n for s, n in ctx.slot_counts().items()}
    players, teams = {}, {}
    for tid, entries in ctx.rosters(refresh=True).items():
        ids = []
        for e in entries:
            r = rows.get(e.player_id, {})
            by_week = {int(k): float(v) for k, v in (r.get("by_week") or {}).items()} if r.get("by_week") else {}
            players[e.player_id] = evaluate.Player(
                player_id=e.player_id, name=e.name, position=e.position, pro_team=e.pro_team,
                eligible=_slot_names(e), team_id=tid, injury=e.injury_status or "ACTIVE", bye_now=bool(e.bye),
                espn_proj=e.projected, espn_avg=e.season_avg, modelled=bool(r.get("modelled")),
                mean=r.get("mean"), by_week=by_week, ros_pg=r.get("ros_pg"), locked=bool(e.lineup_locked),
                slot=e.slot)
            ids.append(e.player_id)
        t = ctx.team(tid)
        teams[tid] = evaluate.Team(team_id=tid, name=ctx.team_name(tid), wins=float(getattr(t, "wins", 0) or 0),
                                   losses=float(getattr(t, "losses", 0) or 0),
                                   points_for=float(getattr(t, "points_for", 0) or 0), roster=ids,
                                   ir=[e.player_id for e in entries if e.slot_id == roster.IR_SLOT])
    # Free agents from the build, so an add can be simulated too.
    for pid, r in rows.items():
        if pid in players or r.get("team_id") not in (None, ""):
            continue
        by_week = {int(k): float(v) for k, v in (r.get("by_week") or {}).items()} if r.get("by_week") else {}
        players[pid] = evaluate.Player(
            player_id=pid, name=r["name"], position=r["position"], pro_team=r.get("pro_team") or "",
            eligible=list(r.get("eligible") or []), team_id=None, injury=r.get("injury") or "ACTIVE",
            bye_now=bool(r.get("bye")), espn_proj=r.get("espn_proj"), espn_avg=r.get("espn_avg"),
            modelled=bool(r.get("modelled")), mean=r.get("mean"), by_week=by_week, ros_pg=r.get("ros_pg"))
    games, seen = [], set()
    history = {}
    for t in lg.teams:
        tid = t.team_id
        history[tid] = [float(s) for s in (t.scores or [])[: max(week - 1, 0)] if s]
        for w in range(week, reg_weeks + 1):
            try:
                opp = t.schedule[w - 1]
            except (IndexError, TypeError):
                continue
            oid = getattr(opp, "team_id", None)
            if oid is None or oid == tid:
                continue
            key = (w, min(tid, oid), max(tid, oid))
            if key not in seen:
                seen.add(key)
                games.append(key)
    byes = {evaluate.NFLVERSE_TO_ESPN.get(t, t): [int(w) for w in ws] for t, ws in (meta.get("team_byes") or {}).items()}
    chart = chart_mod.Chart(chart_mod.load(cfg.data_dir, cfg.year, week))
    state = evaluate.State(week=week, reg_weeks=reg_weeks, playoff_teams=playoff_teams, slot_counts=slot_counts,
                           teams=teams, players=players, games=sorted(games), history=history, me=cfg.team_id,
                           cvs=meta.get("cv") or {}, team_byes=byes, chart=chart if chart else None,
                           data_through=meta.get("data_through"))
    note = ""
    if built_week != week:
        note = f" (forecasts are from the week {built_week} build)"
    return state, note


def thread_summary(team_id):
    """The negotiation thread with a team in one line, for the evaluator's output."""
    row = store.get_negotiation(team_id)
    if not row:
        return None
    last = row["history"][-4:]
    events = "; ".join(f"{h['at'][:10]} {h['event']}" for h in last)
    return (f"[{row['status']}] target {row.get('target') or '-'}; offered {row.get('offered') or '-'}; "
            f"he asks {row.get('their_asks') or '-'}; next {row.get('next_action') or '-'}"
            f"{' by ' + row['next_at'] if row.get('next_at') else ''}. Recent: {events or 'nothing yet'}")


def build(ctx, run):
    cfg = ctx.cfg
    cache = {}

    def state():
        if "state" not in cache:
            cache["state"] = load_state(ctx, cfg)
        return cache["state"]

    def refresh():
        cache.pop("state", None)

    run.refresh_evaluator = refresh

    @tool("playoff_odds",
          "Playoff odds for every team from a season simulation on the model's forecasts, optionally after a "
          "list of changes: [{type: trade, other_team_id, give: [my ids], get: [their ids]}, "
          "{type: add, player_id, drop: [ids]}, {type: drop, drop: [ids]}]. Odds before and after, with any "
          "drops the roster limit would force.", {"changes": list}, READ_ONLY)
    async def playoff_odds(args):
        st, note = state()
        if st is None:
            return err(note)
        try:
            before, after, forced = evaluate.playoff_odds(st, args.get("changes") or [])
        except KeyError as e:
            return err(f"unknown player or team id {e}")
        return text(evaluate.render_odds(st, before, after, forced) + note)

    @tool("evaluate_trade",
          "Judge one trade on the numbers: my playoff odds before and after, both lineups' per-week deltas in "
          "the model's view and in ESPN's view (how the other manager sees it), chart totals, roster fit, "
          "forced drops, byes, and the thread with that manager. give_player_ids are mine, get_player_ids "
          "are theirs. Use it for every offer you consider sending, accepting, or countering.",
          {"other_team_id": int, "give_player_ids": list, "get_player_ids": list}, READ_ONLY)
    async def evaluate_trade(args):
        st, note = state()
        if st is None:
            return err(note)
        other = int(args["other_team_id"])
        ev = evaluate.evaluate_trade(st, other, [int(x) for x in args.get("give_player_ids") or []],
                                     [int(x) for x in args.get("get_player_ids") or []],
                                     ledger=thread_summary(other))
        return text(evaluate.render_trade(ev) + note)

    @tool("market_map",
          "Every team in one view: record, playoff odds, its three weakest starting slots against the league's "
          "typical starter, bench players who would start elsewhere (surplus), byes ahead, open roster "
          "spots, and the negotiation thread with us. Read it before building offers.", {}, READ_ONLY)
    async def market_map(args):
        st, note = state()
        if st is None:
            return err(note)
        threads = {r["team_id"]: thread_summary(r["team_id"]) for r in store.list_negotiations()}
        rows = evaluate.market_map(st, negotiations=threads)
        return text(evaluate.render_market(rows) + note)

    @tool("chart_values",
          "Trade value chart values and bye weeks for a list of player names, from the cached weekly chart.",
          {"names": list}, READ_ONLY)
    async def chart_values(args):
        data = chart_mod.load(cfg.data_dir, cfg.year, ctx.week)
        if not data:
            return err("no trade value chart could be fetched or found on disk")
        ch = chart_mod.Chart(data)
        lines = [f"Chart week {ch.week} ({data.get('source', 'cache')})"]
        for name in args.get("names") or []:
            v = ch.value(name)
            lines.append(f"{name}: {v if v is not None else 'not on chart'}" + (f", bye {ch.bye(name)}" if ch.bye(name) else ""))
        return text("\n".join(lines))

    return [playoff_odds, evaluate_trade, market_map, chart_values]
