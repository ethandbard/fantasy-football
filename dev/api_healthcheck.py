"""
Dev tool: checks that the ESPN Fantasy API is reachable with the configured
league/year/cookies, and previews what data is actually available right now.

Usage (from inside the running container, or any env with espn_api installed
and the same env vars set):

    python dev/api_healthcheck.py

Exits non-zero on failure so it can be used in CI/cron checks later.
"""
import os
import sys

from espn_api.football import League


def load_env():
    required = ["LEAGUE_ID", "LEAGUE_YEAR"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"ERROR: missing required env vars: {missing}")
        sys.exit(1)

    return {
        "league_id": int(os.environ["LEAGUE_ID"]),
        "year": int(os.environ["LEAGUE_YEAR"]),
        "espn_s2": os.environ.get("ESPN_S2"),
        "swid": os.environ.get("SWID"),
    }


def check_connection(cfg):
    print(f"Connecting to league {cfg['league_id']}, year {cfg['year']}...")
    try:
        if cfg["espn_s2"] and cfg["swid"]:
            league = League(
                league_id=cfg["league_id"],
                year=cfg["year"],
                espn_s2=cfg["espn_s2"],
                swid=cfg["swid"],
            )
        else:
            league = League(league_id=cfg["league_id"], year=cfg["year"])
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        print("\nLikely causes:")
        print("  - ESPN_S2 / SWID cookies expired -> re-grab from a logged-in")
        print("    browser session (espn.com -> devtools -> Application -> Cookies)")
        print("  - LEAGUE_ID wrong, or league not yet created for this year")
        print("  - League is private and cookies weren't provided")
        sys.exit(1)

    print("OK: connected\n")
    return league


def report(league):
    print(f"League name:      {league.settings.name}")
    print(f"Team count:       {len(league.teams)}")
    print(f"Current week:     {league.current_week}")
    print(f"Scoring period:   {league.scoringPeriodId}")
    print(f"Matchup periods:  {len(league.settings.matchup_periods)}")

    season_active = league.scoringPeriodId <= len(league.settings.matchup_periods)
    print(f"Season active:    {season_active}")
    if not season_active:
        print("  (this is expected before the draft / season kickoff -- most")
        print("   scheduled bot functions will no-op until this flips True)")

    print("\nTeams:")
    for t in league.teams:
        owner_names = [o.get("displayName", "?") for o in (t.owners or [])]
        print(f"  - {t.team_name:30s} owner(s)={owner_names}")

    print("\nAvailable data shapes (for future visualizations):")
    print(f"  league.box_scores(week)      -> per-matchup home/away score + lineups")
    print(f"  league.teams[i].roster       -> current roster (player objects)")
    print(f"  league.teams[i].scores       -> list of weekly scores this season")
    print(f"  league.teams[i].schedule     -> list of opponents by week")
    print(f"  league.draft                 -> draft picks (once draft has happened)")
    print(f"  league.recent_activity()     -> waiver/trade/add-drop feed")
    print(f"  league.free_agents()         -> available free agent players")

    if league.current_week and league.current_week > 0:
        try:
            box_scores = league.box_scores(week=league.current_week)
            print(f"\nSample: week {league.current_week} box scores ({len(box_scores)} matchups)")
            for b in box_scores[:2]:
                if b.away_team:
                    print(f"  {b.home_team.team_abbrev} {b.home_score} - {b.away_score} {b.away_team.team_abbrev}")
        except Exception as e:
            print(f"\n(could not pull sample box scores yet: {e})")


if __name__ == "__main__":
    cfg = load_env()
    league = check_connection(cfg)
    report(league)
