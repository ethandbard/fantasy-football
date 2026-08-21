"""
Dev tool: backfills a completed season's weekly scores + final standings into
the dashboard's SQLite DB. Useful for adding prior seasons (e.g. 2025) to the
dashboard's year dropdown, since the normal scheduler job only ever collects
the *current* week.

Usage (from inside the running container):

    python dev/backfill_season.py 2025

Reuses LEAGUE_ID / ESPN_S2 / SWID from the environment (same cookies work
across seasons) but takes the year as a CLI arg since LEAGUE_YEAR is fixed
to the current season in the container's env.
"""
import logging
import os
import sys

from espn_api.football import League

sys.path.insert(1, os.path.abspath('.'))
import gamedaybot.espn.collector as collector  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    if len(sys.argv) != 2:
        print("Usage: python dev/backfill_season.py <year>")
        sys.exit(1)

    year = int(sys.argv[1])
    league_id = int(os.environ["LEAGUE_ID"])
    espn_s2 = os.environ.get("ESPN_S2")
    swid = os.environ.get("SWID")

    league = League(league_id=league_id, year=year, espn_s2=espn_s2, swid=swid)
    print(f"Backfilling {league.settings.name} for {year}...")

    collected = collector.collect_historical_season(league)
    if collected:
        print(f"Done. Season {year} is now available in the dashboard dropdown.")
    else:
        print(f"No score data found for {year} -- check the year is correct "
              f"and the season actually completed.")


if __name__ == "__main__":
    main()
