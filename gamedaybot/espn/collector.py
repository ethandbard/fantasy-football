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

    collect_teams(league)
    collect_schedule(league)
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
    # The loop bound must be the last *scoring* period, not the number of
    # matchup periods -- a two-week playoff round is one matchup period that
    # spans two scoring periods, so len(matchup_periods) undercounts weeks
    # once the playoffs start (16 matchup periods but 18 scoring periods).
    periods = league.settings.matchup_periods
    last_week = max(sp for sps in periods.values() for sp in sps)

    collected_any = False
    for week in range(1, last_week + 1):
        if _collect_scores(league, year, week):
            collected_any = True

    if collected_any:
        collect_teams(league)
        collect_schedule(league)
        _collect_standings(league, year, last_week)

    return collected_any


def _scoring_to_matchup_period(league):
    """scoring period -> matchup period lookup, built from settings.matchup_periods."""
    lookup = {}
    for matchup_period, scoring_periods in league.settings.matchup_periods.items():
        for sp in scoring_periods:
            lookup[sp] = int(matchup_period)
    return lookup


def _lineup_score(lineup):
    """Sum of a box score lineup's starters (bench and IR excluded)."""
    return sum(
        p.points for p in lineup if getattr(p, "slot_position", None) not in ("BE", "IR")
    )


def _collect_scores(league, year, week):
    """Returns True if any score rows were written for the given week."""
    try:
        box_scores = league.box_scores(week=week)
    except Exception as e:
        logger.info("Skipping score collection for %s week %s: %s", year, week, e)
        return False

    scoring_to_matchup = _scoring_to_matchup_period(league)
    matchup_period = scoring_to_matchup.get(week, week)
    weeks_in_round = sum(1 for v in scoring_to_matchup.values() if v == matchup_period) or 1

    rows = []
    for b in box_scores:
        if not b.away_team:
            continue

        home_score, away_score = _weekly_scores(b, weeks_in_round)

        rows.append({
            "year": year, "week": week,
            "team_id": b.home_team.team_id, "team_name": b.home_team.team_name,
            "score": home_score, "projected_score": b.home_projected,
            "opponent_id": b.away_team.team_id, "opponent_name": b.away_team.team_name,
            "is_home": 1, "matchup_period": matchup_period, "matchup_score": b.home_score,
        })
        rows.append({
            "year": year, "week": week,
            "team_id": b.away_team.team_id, "team_name": b.away_team.team_name,
            "score": away_score, "projected_score": b.away_projected,
            "opponent_id": b.home_team.team_id, "opponent_name": b.home_team.team_name,
            "is_home": 0, "matchup_period": matchup_period, "matchup_score": b.away_score,
        })

    if not rows:
        return False

    db.upsert_weekly_scores(rows)
    logger.info("Collected %d score rows for %s week %s", len(rows), year, week)
    return True


def _weekly_scores(box, weeks_in_round):
    """
    The single-week score for both sides of a box score.

    For a one-week round, box.home_score/away_score already is the weekly
    score. For a multi-week playoff round, ESPN's box.home_score is the round
    total, so the real per-week number is recovered by summing the starters
    in that week's lineup. Falls back to an even split of the round total if
    the lineups aren't there, logging which path was used since a silent
    fallback would otherwise be invisible.
    """
    if weeks_in_round <= 1:
        return box.home_score, box.away_score

    try:
        home = _lineup_score(box.home_lineup)
        away = _lineup_score(box.away_lineup)
        if home or away:
            return home, away
    except Exception as e:
        logger.info("Lineup sum unavailable for round split, falling back to even "
                    "split: %s", e)

    logger.info("No lineup data to split round total, falling back to an even split "
                "of %s weeks", weeks_in_round)
    return box.home_score / weeks_in_round, box.away_score / weeks_in_round


def collect_teams(league):
    """Upserts team metadata (name, abbrev, logo, owner) for the season."""
    rows = []
    for t in league.teams:
        owner_names = [o.get("displayName", "") for o in (t.owners or [])]
        rows.append({
            "year": league.year, "team_id": t.team_id, "team_name": t.team_name,
            "abbrev": getattr(t, "team_abbrev", None),
            "logo_url": getattr(t, "logo_url", None),
            "owner": ", ".join(n for n in owner_names if n) or None,
        })

    if rows:
        db.upsert_teams(rows)
        logger.info("Collected %d team rows for %s", len(rows), league.year)


def collect_schedule(league):
    """
    Upserts the full season schedule, keyed off each week's box scores so
    is_home comes from ESPN directly rather than being guessed.
    """
    scoring_to_matchup = _scoring_to_matchup_period(league)
    last_week = max(sp for sps in league.settings.matchup_periods.values() for sp in sps)

    rows = []
    for week in range(1, last_week + 1):
        try:
            box_scores = league.box_scores(week=week)
        except Exception as e:
            logger.info("Skipping schedule collection for %s week %s: %s",
                        league.year, week, e)
            continue

        matchup_period = scoring_to_matchup.get(week, week)
        for b in box_scores:
            if not b.away_team:
                continue
            rows.append({
                "year": league.year, "week": week, "matchup_period": matchup_period,
                "team_id": b.home_team.team_id, "opponent_id": b.away_team.team_id,
                "is_home": 1,
            })
            rows.append({
                "year": league.year, "week": week, "matchup_period": matchup_period,
                "team_id": b.away_team.team_id, "opponent_id": b.home_team.team_id,
                "is_home": 0,
            })

    if rows:
        db.upsert_schedule(rows)
        logger.info("Collected %d schedule rows for %s", len(rows), league.year)


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
