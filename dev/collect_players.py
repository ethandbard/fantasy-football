"""
Dev tool: pull the ESPN player pool (ranks, bye, projected FPTS and counting
stats) into the dashboard's SQLite DB.

The full container also does this on startup and every morning. Use this
when running the dashboard alone (`shiny run`) so the Draft page has data
without standing up the scheduler.

Usage (from inside the running container, or any env with espn_api installed
and the same env vars set):

    python dev/collect_players.py
    python dev/collect_players.py 2026
"""
import logging
import os
import sys

from espn_api.football import League

sys.path.insert(1, os.path.abspath("."))
import gamedaybot.espn.collector as collector  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("LEAGUE_YEAR", 2026))
    league_id = int(os.environ["LEAGUE_ID"])
    espn_s2 = os.environ.get("ESPN_S2")
    swid = os.environ.get("SWID")

    league = League(league_id=league_id, year=year, espn_s2=espn_s2, swid=swid)
    print(f"Collecting player pool, teams, and draft for {league.settings.name} ({year})...")

    collected = collector.collect_league_state(league)
    if not collected:
        print("No players or draft picks returned -- check LEAGUE_ID, cookies, and year.")
        sys.exit(1)

    import gamedaybot.storage.db as db
    rows = [r for r in db.get_all_players() if r["year"] == year]
    picks = [r for r in db.get_all_draft_picks() if r["year"] == year]
    teams = [r for r in db.get_all_teams() if r["year"] == year]
    print(f"Stored {len(rows)} players, {len(teams)} teams, {len(picks)} draft picks.")
    for r in rows[:8]:
        rank = r["draft_rank"] if r["draft_rank"] is not None else "—"
        fpts = r["projected_points"] if r["projected_points"] is not None else "—"
        bye = r["bye_week"] if r["bye_week"] is not None else "—"
        print(f"  {rank:>4}  {r['position']:<4} {r['name']:<24} {r['pro_team']:<4} "
              f"bye {bye:<3} FPTS {fpts}")


if __name__ == "__main__":
    main()
