"""
Collects a snapshot of the current week's scores and standings and persists
them to SQLite, so the dashboard has historical data to chart instead of only
ever seeing "right now".
"""
import logging

import gamedaybot.storage.db as db

logger = logging.getLogger(__name__)


def collect_weekly_snapshot(league):
    """
    Pulls the current week's box scores and standings from `league` and
    upserts them into the local database. Safe to call multiple times for
    the same week (idempotent via INSERT OR REPLACE on (year, week, team_id)).

    No-ops (with a log message) if the season hasn't started yet. Standings
    are gated on scores because league.standings() succeeds before the draft
    even though box_scores() doesn't -- collecting it unconditionally writes
    a meaningless 0-0 snapshot of however many teams have joined so far.
    """
    db.init_db()
    year = league.year
    week = league.current_week

    # Fill any earlier week we're missing, so a container that was down for a
    # Tuesday -- or a mid-season LEAGUE_YEAR change -- repairs itself instead
    # of leaving a permanent hole. The current week is always re-collected,
    # since its scores may have been corrected since the last run.
    already_have = db.get_collected_weeks(year)
    collected = False
    for w in range(1, week + 1):
        if w in already_have and w != week:
            continue
        if _collect_scores(league, year, w):
            collected = True

    if not collected:
        logger.info("No box scores for %s through week %s -- skipping standings "
                    "(season likely hasn't started)", year, week)
        return

    _collect_standings(league, year, week)


def collect_historical_season(league):
    """
    Backfills every completed week of a past season. Unlike
    collect_weekly_snapshot (which only looks at the league's "current"
    week), this loops week-by-week since a finished season's box scores are
    only available per-week, not via a single "current" snapshot.

    Standings are only meaningful as a final-season snapshot (ESPN doesn't
    expose historical week-by-week standings), so they're collected once
    using the last completed week.
    """
    db.init_db()
    year = league.year
    last_week = len(league.settings.matchup_periods)

    collected_any = False
    for week in range(1, last_week + 1):
        if _collect_scores(league, year, week):
            collected_any = True

    if collected_any:
        _collect_standings(league, year, last_week)

    return collected_any


def _collect_scores(league, year, week):
    """Returns True if any score rows were written for the given week."""
    try:
        box_scores = league.box_scores(week=week)
    except Exception as e:
        logger.info("Skipping score collection for %s week %s: %s", year, week, e)
        return False

    rows = []
    for b in box_scores:
        if not b.away_team:
            continue
        rows.append({
            "year": year, "week": week,
            "team_id": b.home_team.team_id, "team_name": b.home_team.team_name,
            "score": b.home_score, "projected_score": b.home_projected,
            "opponent_id": b.away_team.team_id, "opponent_name": b.away_team.team_name,
            "is_home": 1,
        })
        rows.append({
            "year": year, "week": week,
            "team_id": b.away_team.team_id, "team_name": b.away_team.team_name,
            "score": b.away_score, "projected_score": b.away_projected,
            "opponent_id": b.home_team.team_id, "opponent_name": b.home_team.team_name,
            "is_home": 0,
        })

    if not rows:
        return False

    db.upsert_weekly_scores(rows)
    logger.info("Collected %d score rows for %s week %s", len(rows), year, week)
    return True


def _collect_standings(league, year, week):
    try:
        standings = league.standings()
    except Exception as e:
        logger.info("Skipping standings collection for %s week %s: %s", year, week, e)
        return

    rows = [
        {
            "year": year, "week": week,
            "team_id": team.team_id, "team_name": team.team_name,
            "wins": team.wins, "losses": team.losses, "ties": team.ties,
            "points_for": team.points_for, "points_against": team.points_against,
            "rank": pos + 1,
        }
        for pos, team in enumerate(standings)
    ]

    if rows:
        db.upsert_standings(rows)
        logger.info("Collected %d standings rows for %s week %s", len(rows), year, week)
