"""
Dev tool: tie every season's teams to the managers who ran them.

Two steps, both safe to repeat:

1. Re-collects the teams rows for every season in the database, so each
   carries ESPN's owner GUID -- the key that follows a manager through team
   renames and reassigned team ids. Seasons collected before that column
   existed have it empty until this runs.
2. Reads a names file ("2025" / "Team name - Person" lines; see
   gamedaybot/espn/managers.py) and stores what the league calls each
   manager. Without a file, ESPN's first names are used.

Usage (from inside the running container, or any env with espn_api installed
and the same env vars set):

    python dev/set_managers.py                     # GUIDs + data/managers.txt if present
    python dev/set_managers.py path/to/names.txt
    python dev/set_managers.py --no-refresh        # names only, no ESPN calls

The names file holds real first names, so it lives under data/ (ignored by
git) rather than in the repository.
"""
import logging
import os
import sys

sys.path.insert(1, os.path.abspath("."))
import pandas as pd  # noqa: E402

import gamedaybot.espn.collector as collector  # noqa: E402
from gamedaybot.espn import managers  # noqa: E402
from gamedaybot.storage import db  # noqa: E402
from gamedaybot.web import stats  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_NAMES = os.path.join(os.path.dirname(db.DB_PATH), "managers.txt")


def refresh_teams():
    from espn_api.football import League

    league_id = int(os.environ["LEAGUE_ID"])
    years = sorted({int(t["year"]) for t in db.get_all_teams()} | set(db.get_years()))
    for year in years:
        league = League(league_id=league_id, year=year,
                        espn_s2=os.environ.get("ESPN_S2"), swid=os.environ.get("SWID"))
        collector.collect_teams(league)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else DEFAULT_NAMES
    db.init_db()

    if "--no-refresh" not in sys.argv:
        refresh_teams()

    teams = db.get_all_teams()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            names, problems = managers.resolve(managers.parse_names(f.read()), teams)
        db.upsert_managers(names)
        print(f"Named {len(names)} managers from {path}.")
        for p in problems:
            print("  note:", p)
    elif args:
        print(f"No names file at {path}.")
        sys.exit(1)
    else:
        print(f"No names file at {path}; using ESPN first names.")

    table = stats.managers(pd.DataFrame(db.get_all_weekly_scores()), pd.DataFrame(teams),
                           db.get_managers())
    for _, seasons in table.groupby("mid"):
        who = seasons["manager"].iloc[0] or "(unnamed)"
        history = ", ".join(f"{r.year} {r.team_name}" for r in seasons.itertuples())
        print(f"  {who:<12} {history}".encode("ascii", "replace").decode())


if __name__ == "__main__":
    main()
