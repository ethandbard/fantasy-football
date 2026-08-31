"""
Collects a snapshot of the current week's scores and standings and persists
them to SQLite, so the dashboard has historical data to chart instead of only
ever seeing "right now".
"""
import logging
from urllib.parse import urlparse

import requests

import gamedaybot.espn.players as players
import gamedaybot.storage.db as db

logger = logging.getLogger(__name__)


def collect_weekly_snapshot(league):
    """
    Pulls the current week's box scores and standings from `league` and
    upserts them into the local database. Safe to call multiple times for
    the same week (idempotent via INSERT OR REPLACE on (year, week, team_id)).

    Always refreshes the player pool first -- that data exists before the
    draft. Score and standings collection no-ops (with a log message) if
    the season hasn't started yet. Standings are gated on scores because
    league.standings() succeeds before the draft even though box_scores()
    doesn't -- collecting it unconditionally writes a meaningless 0-0
    snapshot of however many teams have joined so far.
    """
    db.init_db()
    year = league.year
    week = league.current_week
    collect_league_state(league)

    # Fill any earlier week we're missing, so a container that was down for a
    # Tuesday -- or a mid-season LEAGUE_YEAR change -- repairs itself instead
    # of leaving a permanent hole. "Missing" also covers a week whose rows
    # predate a column the dashboard now needs, so a schema addition repairs
    # the season behind it rather than leaving the weeks either side of the
    # change reading differently. The current week is always re-collected,
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
    collect_league_state(league)
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
        _collect_standings(league, year, last_week)

    # The whole season's trades in one pull. Backfill inserts them silently;
    # the hourly in-season job only announces rows *it* was first to insert,
    # so nothing here can trigger a Discord post.
    collect_trades(league, size=200)

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


def collect_player_pool(league):
    """
    Replace the stored player pool for league.year.

    Independent of box scores, so it still runs before the draft. FPTS uses
    this league's scoring settings via kona_player_info.
    """
    db.init_db()
    year = league.year
    try:
        schedule = league._get_all_pro_schedule()
    except Exception as e:
        logger.info("Pro schedule unavailable for bye weeks: %s", e)
        schedule = {}

    try:
        entries, rank_type = players.fetch_player_pool(league)
    except Exception as e:
        logger.info("Skipping player pool collection for %s: %s", year, e)
        return False

    bye_by_team = players.bye_weeks_from_schedule(schedule)
    rows = players.parse_player_pool(entries, year, bye_by_team, rank_type)
    if not rows:
        logger.info("Player pool for %s was empty", year)
        return False

    db.replace_players(year, rows)
    logger.info("Collected %d players for %s (%s ranks)", len(rows), year, rank_type)
    return True


def collect_league_state(league):
    """
    Player pool, team names, draft picks, and the season schedule.

    These exist as soon as the draft is in, so they are not gated on box
    scores the way standings are. The schedule lives here rather than behind
    the score gate so the Next up page has matchups and projections before
    week 1 is ever played, and so its projections refresh with the daily
    player-pool job instead of only on Tuesdays.
    """
    collected = collect_player_pool(league)
    collect_teams(league)
    collect_logos(league)
    collect_schedule(league)
    collected = collect_draft_picks(league) or collected
    return collected


def collect_draft_picks(league):
    """Replace stored draft picks for league.year. No-ops if ESPN has none yet."""
    db.init_db()
    picks = getattr(league, "draft", None) or []
    n_teams = len(league.teams) or 1
    rows = []
    for p in picks:
        team = getattr(p, "team", None)
        round_num = int(getattr(p, "round_num", 0) or 0)
        round_pick = int(getattr(p, "round_pick", 0) or 0)
        overall = (round_num - 1) * n_teams + round_pick if round_num and round_pick else 0
        rows.append({
            "year": league.year,
            "overall_pick": overall,
            "round_num": round_num,
            "round_pick": round_pick,
            "team_id": getattr(team, "team_id", None) if team is not None else None,
            "team_name": getattr(team, "team_name", None) if team is not None else None,
            "player_id": getattr(p, "playerId", None),
            "player_name": getattr(p, "playerName", None),
            "bid_amount": getattr(p, "bid_amount", None) or 0,
            "keeper": 1 if getattr(p, "keeper_status", False) else 0,
        })
    rows = [r for r in rows if r["player_id"] is not None and r["overall_pick"]]
    if not rows:
        logger.info("No draft picks for %s yet", league.year)
        return False
    db.replace_draft_picks(league.year, rows)
    logger.info("Collected %d draft picks for %s", len(rows), league.year)
    return True


def _trade_rows_from_activity(activity, year):
    """
    One trade Activity flattened to a row per player moved.

    espn_api emits a TRADE_SENT action (from the sending team) and a
    TRADE_RECEIVED action (to the receiving team) for each player in the
    trade; this pairs them up by player id. The player slot can be a bare id
    when ESPN no longer knows the player, so every attribute read has a
    fallback.
    """
    by_player = {}
    for team, action, player, _bid in activity.actions:
        if action not in ("TRADE_SENT", "TRADE_RECEIVED"):
            continue
        player_id = getattr(player, "playerId", None)
        if player_id is None:
            player_id = player if isinstance(player, int) else hash(str(player))
        row = by_player.setdefault(player_id, {
            "year": year, "trade_date": activity.date, "player_id": player_id,
            "player_name": getattr(player, "name", str(player)),
            "position": getattr(player, "position", None),
            "from_team_id": None, "from_team_name": None,
            "to_team_id": None, "to_team_name": None,
        })
        side = "from" if action == "TRADE_SENT" else "to"
        row[f"{side}_team_id"] = getattr(team, "team_id", None)
        row[f"{side}_team_name"] = getattr(team, "team_name", None)
    return list(by_player.values())


def collect_trades(league, size=50):
    """
    Pulls trade activity into the trades table and returns only the rows
    that were new this call -- the hourly check announces exactly those.

    Requires the league cookies (ESPN treats transactions as private); a
    public-league or failed fetch logs and returns nothing rather than
    crashing the job.
    """
    db.init_db()
    try:
        activities = league.recent_activity(size, msg_type="TRADED")
    except Exception as e:
        logger.info("Skipping trade collection for %s: %s", league.year, e)
        return []

    rows = []
    for activity in activities:
        rows.extend(_trade_rows_from_activity(activity, league.year))

    if not rows:
        return []

    new_rows = db.insert_new_trades(rows)
    if new_rows:
        logger.info("Collected %d new trade rows for %s", len(new_rows), league.year)
    return new_rows


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


# ESPN's default_logos are the grey silhouette every un-customised team gets.
# Storing those would put the same non-logo on half the league, so they are
# treated as "no logo" and the dashboard draws its coloured monogram instead.
_DEFAULT_LOGO_MARKER = "/default_logos/"

# Guessed from the URL when the server doesn't say. ESPN's logo-pack picks
# are SVG; user uploads are usually PNG or JPEG.
_LOGO_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def wants_logo_fetch(url, stored_url):
    """Whether a team's logo needs downloading: it has a real (non-default)
    URL that differs from whatever is already stored."""
    if not url or _DEFAULT_LOGO_MARKER in url:
        return False
    return url != stored_url


def logo_request_cookies(league, url):
    """
    The league's ESPN auth cookies, but only for ESPN's own hosts.

    User-uploaded logos live on mystique-api.fantasy.espn.com and return 401
    without the league cookies. A team can also point its logo anywhere on
    the internet, and those hosts must never see the ESPN session.
    """
    host = (urlparse(url).hostname or "").lower()
    if host != "espn.com" and not host.endswith(".espn.com") \
            and not host.endswith(".espncdn.com"):
        return {}
    return getattr(getattr(league, "espn_request", None), "cookies", None) or {}


def _logo_content_type(url, response):
    declared = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    if declared.startswith("image/"):
        return declared
    lowered = url.lower().split("?")[0]
    for ext, ctype in _LOGO_TYPES.items():
        if lowered.endswith(ext):
            return ctype
    return "image/png"


def collect_logos(league):
    """
    Downloads each team's logo into the database, once per URL.

    Runs with the daily league-state job, but the URL check in
    wants_logo_fetch means a normal morning downloads nothing -- only a new
    team, a changed logo, or a fresh database costs a fetch. Failures are
    logged and skipped: a missing logo falls back to the monogram, which is
    strictly better than a crashed collect.
    """
    db.init_db()
    stored = db.get_logo_urls()
    for t in league.teams:
        url = getattr(t, "logo_url", None)
        if not wants_logo_fetch(url, stored.get((league.year, t.team_id))):
            continue
        try:
            response = requests.get(url, timeout=10,
                                    cookies=logo_request_cookies(league, url))
            response.raise_for_status()
        except Exception as e:
            logger.info("Skipping logo for %s (%s): %s", t.team_name, url, e)
            continue
        db.upsert_team_logo(
            league.year, t.team_id, url,
            response.content, _logo_content_type(url, response),
        )
        logger.info("Collected logo for %s (%d bytes)", t.team_name,
                    len(response.content))


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
                "is_home": 1, "projected_score": getattr(b, "home_projected", None),
            })
            rows.append({
                "year": league.year, "week": week, "matchup_period": matchup_period,
                "team_id": b.away_team.team_id, "opponent_id": b.home_team.team_id,
                "is_home": 0, "projected_score": getattr(b, "away_projected", None),
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
