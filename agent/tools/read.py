"""
Read-only league tools. Safe for every persona, including the league analyst
that answers friends, except the ones listed in PRIVATE, which expose Ethan's
own research and plans.
"""
import json
from datetime import datetime

from claude_agent_sdk import ToolAnnotations, tool

import gamedaybot.espn.roster as roster
from agent import store
from agent.tools.common import err, text

NAMES = [
    "get_rules", "get_my_roster", "get_team_roster", "list_teams", "get_free_agents",
    "get_matchup", "get_standings", "get_pending_transactions", "get_recent_activity",
    "get_kickoffs", "get_player", "get_week_results", "read_research", "read_state",
    "read_season_log", "read_briefs", "get_run_context",
]
PRIVATE = ["read_research", "read_state", "read_season_log", "read_briefs", "get_run_context", "get_rules"]

READ_ONLY = ToolAnnotations(readOnlyHint=True)


def _fmt_entries(entries):
    return roster.to_table(entries) + "\n\nJSON:\n" + json.dumps([e.to_dict() for e in entries])


def build(ctx, run):
    cfg = ctx.cfg

    @tool("get_rules", "The permission rules as data: core players, deadline, roster ceilings, caps.", {}, READ_ONLY)
    async def get_rules(args):
        rules = {k: v for k, v in ctx.rules.items() if k != "core_players_lower"}
        return text(json.dumps(rules, indent=2))

    @tool("get_run_context", "What this run is for: job, week, scoring period, dry-run flag, and parameters.", {}, READ_ONLY)
    async def get_run_context(args):
        return text(json.dumps({
            "job": run.job, "run_id": run.run_id, "week": ctx.week, "scoring_period": ctx.scoring_period,
            "team_id": cfg.team_id, "team": ctx.team_name(cfg.team_id), "writes_enabled": ctx.write_enabled,
            "now_utc": datetime.utcnow().isoformat(timespec="minutes"), "params": run.params,
        }, indent=2))

    @tool("get_my_roster", "My roster with slots, slot ids, injury tags, lock flags, projections, and kickoffs.", {}, READ_ONLY)
    async def get_my_roster(args):
        entries = ctx.my_roster(refresh=True)
        counts = ctx.slot_counts()
        problems = roster.lineup_problems(entries, counts)
        head = f"Team {ctx.team_name(cfg.team_id)} (id {cfg.team_id}), scoring period {ctx.scoring_period}\n"
        head += "Starting slots: " + ", ".join(f"{roster.slot_name(s)} x{n} (slot id {s})" for s, n in counts.items())
        head += "\nBench slot id 20, IR slot id 21\n"
        if problems:
            head += "Lineup problems: " + "; ".join(problems) + "\n"
        return text(head + "\n" + _fmt_entries(entries))

    @tool("get_team_roster", "Another team's roster with the same detail as mine.",
          {"team_id": int}, READ_ONLY)
    async def get_team_roster(args):
        entries = ctx.rosters().get(int(args["team_id"]))
        if entries is None:
            return err(f"no team {args['team_id']}")
        return text(f"Team {ctx.team_name(args['team_id'])} (id {args['team_id']})\n" + _fmt_entries(entries))

    @tool("list_teams", "Every team: id, name, owner, record, points, standing, waiver rank.", {}, READ_ONLY)
    async def list_teams(args):
        return text(json.dumps(ctx.teams_summary(), indent=2))

    @tool("get_free_agents",
          "Free agents and waiver-wire players. position: QB, RB, WR, TE, FLEX, D/ST, K, or omit for all. "
          "sort: projected, season_avg, last_week, or owned. status WAIVERS means a claim is needed.",
          {"position": str, "size": int, "sort": str}, READ_ONLY)
    async def get_free_agents(args):
        try:
            rows = ctx.free_agents(args.get("position") or None, int(args.get("size") or 15), args.get("sort") or "projected")
        except ValueError as e:
            return err(str(e))
        lines = []
        for d in rows:
            flags = []
            if d["injury_status"] != "ACTIVE":
                flags.append(d["injury_status"])
            if d["bye"]:
                flags.append("BYE")
            lines.append(
                f"{d['name']:<24} {d['position']:<4} {d['pro_team']:<4} {d['status']:<9} "
                f"proj {d['projected'] if d['projected'] is not None else '-':<6} "
                f"last {d['last_week'] if d['last_week'] is not None else '-':<6} "
                f"avg {d['season_avg'] if d['season_avg'] is not None else '-':<6} "
                f"own {d['percent_owned']:<5} id {d['player_id']} {' '.join(flags)}".rstrip()
            )
        return text("\n".join(lines) + "\n\nJSON:\n" + json.dumps(rows))

    def team_arg(args):
        """team_id from the call, defaulting to the team this service manages."""
        raw = args.get("team_id")
        return int(raw) if raw not in (None, "", 0) else cfg.team_id

    @tool("get_matchup",
          "A team's matchup for a week (default current): opponent, projections, both lineups. "
          "team_id defaults to the managed team; pass another team's id for theirs.",
          {"week": int, "team_id": int}, READ_ONLY)
    async def get_matchup(args):
        lg = ctx.league()
        week = int(args.get("week") or lg.current_week)
        team_id = team_arg(args)
        for box in lg.box_scores(week):
            teams = {box.home_team.team_id: ("home", box), box.away_team.team_id: ("away", box)}
            if team_id not in teams:
                continue
            side, _ = teams[team_id]
            me, opp = (box.home_team, box.away_team) if side == "home" else (box.away_team, box.home_team)
            my_lineup, opp_lineup = (box.home_lineup, box.away_lineup) if side == "home" else (box.away_lineup, box.home_lineup)
            my_score, opp_score = (box.home_score, box.away_score) if side == "home" else (box.away_score, box.home_score)
            my_proj, opp_proj = (box.home_projected, box.away_projected) if side == "home" else (box.away_projected, box.home_projected)

            def fmt(lineup):
                return "\n".join(
                    f"{p.slot_position:<8} {p.name:<24} {p.position:<4} {p.proTeam:<4} "
                    f"vs {p.pro_opponent:<4} proj {p.projected_points:<6} pts {p.points:<6} {p.injuryStatus}"
                    for p in lineup)
            body = (f"Week {week}: {me.team_name} ({me.wins}-{me.losses}) vs {opp.team_name} ({opp.wins}-{opp.losses})\n"
                    f"Score {my_score} - {opp_score}; projected {my_proj} - {opp_proj}\n\n"
                    f"{me.team_name} lineup:\n{fmt(my_lineup)}\n\n{opp.team_name} lineup:\n{fmt(opp_lineup)}")
            return text(body)
        return err(f"no matchup for team {team_id} in week {week}")

    @tool("get_standings", "League standings.", {}, READ_ONLY)
    async def get_standings(args):
        rows = ctx.teams_summary()
        lines = [f"{r['standing']:>2}. {r['name']:<32} {r['record']:<6} PF {r['points_for']:<7} PA {r['points_against']:<7} waiver #{r['waiver_rank']}"
                 for r in rows]
        return text("\n".join(lines))

    @tool("get_pending_transactions",
          "Pending waiver claims and trade offers league-wide, with who is involved and team names on each "
          "item. Read `phase` before calling a trade unaccepted: ESPN keeps an accepted trade at status "
          "PENDING through the league's review window, and `phase` says so with the time it processes.",
          {}, READ_ONLY)
    async def get_pending_transactions(args):
        return text(json.dumps(ctx.pending_transactions(), indent=2))

    @tool("get_recent_activity", "Recent league moves: adds, drops, waivers, trades.", {"size": int}, READ_ONLY)
    async def get_recent_activity(args):
        rows = ctx.recent_activity(int(args.get("size") or 25))
        return text("\n".join(f"{r['when']}  {r['team']:<32} {r['action']:<16} {r['player']}" for r in rows))

    @tool("get_kickoffs",
          "Distinct kickoff times this week for a team's rostered players, with who plays when. "
          "team_id defaults to the managed team.", {"team_id": int}, READ_ONLY)
    async def get_kickoffs(args):
        team_id = team_arg(args)
        entries = ctx.rosters().get(team_id)
        if entries is None:
            return err(f"no team {team_id}")
        groups = roster.distinct_kickoffs(entries)
        lines = [f"{when.isoformat(timespec='minutes')}  ({when.astimezone(roster._eastern()).strftime('%a %I:%M %p ET')}): {', '.join(names)}"
                 for when, names in groups]
        return text("\n".join(lines) or f"no games found for {ctx.team_name(team_id)} this period")

    @tool("get_player", "Look up one player by name: team, position, injury, ownership, season and weekly points.",
          {"name": str}, READ_ONLY)
    async def get_player(args):
        try:
            p = ctx.league().player_info(name=args["name"])
        except Exception as e:
            return err(f"lookup failed: {e}")
        if not p:
            return err(f"no player named {args['name']}")
        weeks = {k: v.get("points") for k, v in sorted(p.stats.items()) if k}
        team_id = p.onTeamId
        return text(json.dumps({
            "player_id": p.playerId, "name": p.name, "position": p.position, "pro_team": p.proTeam,
            "injury_status": p.injuryStatus, "injured": p.injured, "percent_owned": p.percent_owned,
            "on_team": ctx.team_name(team_id) if team_id else "free agent",
            "season_points": p.total_points, "season_avg": p.avg_points, "projected_avg": p.projected_avg_points,
            "weekly_points": weeks, "eligible_slots": p.eligibleSlots,
        }, indent=2))

    @tool("get_week_results",
          "A team's box score for a played week: each starter's projection vs actual, and the result. "
          "team_id defaults to the managed team.",
          {"week": int, "team_id": int}, READ_ONLY)
    async def get_week_results(args):
        lg = ctx.league()
        week = int(args.get("week") or max(1, lg.current_week - 1))
        team_id = team_arg(args)
        for box in lg.box_scores(week):
            ids = {box.home_team.team_id, box.away_team.team_id}
            if team_id not in ids:
                continue
            home = box.home_team.team_id == team_id
            me, opp = (box.home_team, box.away_team) if home else (box.away_team, box.home_team)
            mine = box.home_lineup if home else box.away_lineup
            my_score = box.home_score if home else box.away_score
            opp_score = box.away_score if home else box.home_score
            lines = [f"Week {week}: {me.team_name} {my_score} vs {opp.team_name} {opp_score}"]
            for p in mine:
                lines.append(f"{p.slot_position:<8} {p.name:<24} proj {p.projected_points:<6} actual {p.points:<6} {p.injuryStatus}")
            return text("\n".join(lines))
        return err(f"no box score for week {week}")

    @tool("read_research", "The research file for a week (default current). Written by the Tuesday research job.",
          {"week": int}, READ_ONLY)
    async def read_research(args):
        week = int(args.get("week") or ctx.week)
        path = cfg.data_dir / "research" / f"week-{week:02d}.md"
        if not path.exists():
            return text(f"no research file for week {week}")
        return text(path.read_text(encoding="utf-8"))

    @tool("read_state", "The structured state file for a week (default current): per-player notes, tiers, other owners' needs.",
          {"week": int}, READ_ONLY)
    async def read_state(args):
        week = int(args.get("week") or ctx.week)
        path = cfg.data_dir / "state" / f"week-{week:02d}.json"
        if not path.exists():
            return text(f"no state file for week {week}")
        return text(path.read_text(encoding="utf-8"))

    @tool("read_briefs",
          "Recent briefs the managing agent wrote, newest first: trade reviews, roster plans, research "
          "digests, post-waiver, designation, and pre-game notes. Optional job filter (trade_review, plan, "
          "research, postwaiver, designations, pregame, lineup) and limit (default 6).",
          {"job": str, "limit": int}, READ_ONLY)
    async def read_briefs(args):
        rows = store.recent_briefs(limit=int(args.get("limit") or 6), job=args.get("job") or None)
        if not rows:
            return text("no briefs yet")
        parts = [f"## {r['job']} · {r['started_at']}\n\n{r['result']}" for r in rows]
        return text("\n\n---\n\n".join(parts)[:20000])

    @tool("read_season_log", "The tail of the season log (default last 12000 characters).", {"chars": int}, READ_ONLY)
    async def read_season_log(args):
        path = cfg.data_dir / "season-log.md"
        if not path.exists():
            return text("season log is empty")
        body = path.read_text(encoding="utf-8")
        n = int(args.get("chars") or 12000)
        return text(body[-n:])

    return [get_rules, get_run_context, get_my_roster, get_team_roster, list_teams, get_free_agents,
            get_matchup, get_standings, get_pending_transactions, get_recent_activity, get_kickoffs,
            get_player, get_week_results, read_research, read_state, read_briefs, read_season_log]
