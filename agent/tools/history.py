"""
League history tools: every past season, read from the dashboard's database
rather than from ESPN, whose League object only knows the current year.

Public, like the rest of the analyst's tools -- it is all on the dashboard.
History is between managers, not team names: most of the league renames its
team every year, and ESPN reassigns a departed manager's team id, so the
database ties each team-season to its owner's GUID (see stats.managers).
"""
import json

from claude_agent_sdk import ToolAnnotations, tool

from agent.tools.common import err, text
from gamedaybot.storage import db

NAMES = ["get_league_history", "get_rivalry"]

READ_ONLY = ToolAnnotations(readOnlyHint=True)


def _frames():
    # Imported here so the tool modules still load where pandas is absent.
    import pandas as pd

    from gamedaybot.web import stats
    return (stats, pd.DataFrame(db.get_all_weekly_scores()), pd.DataFrame(db.get_all_teams()),
            db.get_managers())


def build(ctx, run):
    cfg = ctx.cfg

    @tool("get_league_history",
          "Every manager across every season on file: first name, the team name they used each year, "
          "that season's record, points and finish by record, and each season's champion. Use it to "
          "tell a returning manager from a new one and to follow a team through its renames.",
          {}, READ_ONLY)
    async def get_league_history(args):
        stats, scores, teams, names = _frames()
        index = stats.managers(scores, teams, names, as_of_year=cfg.year)
        if index.empty:
            return text("no league history on file")
        records = {}
        if not scores.empty:
            for year, season in scores.groupby("year"):
                for place, r in enumerate(stats.derive_records(season).itertuples(), start=1):
                    records[(int(year), int(r.team_id))] = {
                        "record": r.record, "points_for": r.points_for, "place_by_record": place}
        champions = stats.champions(scores, db.get_all_league_settings()) if not scores.empty else {}
        out = []
        for _, seasons in index.groupby("mid", sort=False):
            out.append({
                "manager": seasons["manager"].iloc[0],
                "current_team": seasons["label"].iloc[0],
                "in_league_this_season": bool((seasons["year"] == cfg.year).any()),
                "seasons": [{"year": int(s.year), "team_id": int(s.team_id), "team_name": s.team_name,
                             **records.get((int(s.year), int(s.team_id)), {})}
                            for s in seasons.itertuples()],
            })
        body = {"note": "place_by_record counts every scored week, playoffs included; "
                        "champions is the title winner by season.",
                "champions": {int(y): n for y, n in champions.items()}, "managers": out}
        return text(json.dumps(body, indent=2, ensure_ascii=False))

    @tool("get_rivalry",
          "The all-time history between two of this season's teams, across every season and through "
          "every team rename: series record, current streak, every past meeting with the names and "
          "scores of the day (a meeting spanning two weeks is one playoff round decided on the combined "
          "score), postseason meetings, biggest and closest games. team_id and opponent_id "
          "are this season's ids. Pass before_week to leave that week and later out -- the week being "
          "previewed or recapped -- so the game itself is not counted as history.",
          {"team_id": int, "opponent_id": int, "before_week": int}, READ_ONLY)
    async def get_rivalry(args):
        try:
            team_id, opponent_id = int(args["team_id"]), int(args["opponent_id"])
        except (KeyError, TypeError, ValueError):
            return err("team_id and opponent_id are required")
        stats, scores, teams, names = _frames()
        reg = {int(y): int(s["reg_season_count"]) for y, s in db.get_all_league_settings().items()
               if s.get("reg_season_count")}
        before = (cfg.year, int(args["before_week"])) if args.get("before_week") else None
        r = stats.rivalry(scores, teams, cfg.year, team_id, opponent_id, names, reg, before)
        if r["a"]["mid"] is None or r["b"]["mid"] is None:
            return err(f"no team {team_id} or {opponent_id} on file for {cfg.year}")
        head = (f"{r['a']['label']} ({r['a']['manager'] or 'manager unknown'}, in the league since "
                f"{r['a']['first_year']}) vs {r['b']['label']} ({r['b']['manager'] or 'manager unknown'}, "
                f"since {r['b']['first_year']})")
        lines = [head, *stats.rivalry_notes(r)]
        if r["meetings"]:
            lines.append(f"Average margin for {r['a']['label']}: {r['avg_margin']:+.1f}")
            lines.append("Meetings, oldest first:")
            lines += [f"  {stats.meeting_when(m)}{' (postseason)' if m['postseason'] else ''}: "
                      f"{m['a_name']} {m['a_score']:.1f} - {m['b_score']:.1f} {m['b_name']}"
                      for m in r["meetings"]]
        return text("\n".join(lines))

    return [get_league_history, get_rivalry]
