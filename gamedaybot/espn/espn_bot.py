"""
Generates a named report and posts it to the Discord webhook.

The scheduler drives this. The slash-command bot in gamedaybot.discord_bot.bot
serves the same reports over its gateway connection, calling the same
functionality.py helpers and the same embed builders so the two stay identical.
"""
import logging
import time

from espn_api.football import League

import gamedaybot.discord_bot.formatting as discord_fmt
import gamedaybot.espn.collector as collector
import gamedaybot.espn.functionality as espn
from gamedaybot.discord_bot.webhook import Discord
from gamedaybot.espn.env_vars import NO_ESPN_S2, NO_SWID, get_env_vars

logger = logging.getLogger(__name__)

# A newly stored trade older than this is seeded silently instead of
# announced. The hourly check makes this moot in normal operation; it only
# bites on a first deploy mid-season (or after long downtime), where posting
# a backlog of week-old "Trade Alert" messages would read as spam.
TRADE_ANNOUNCE_WINDOW_SECONDS = 3 * 24 * 3600

# Maps a report name to its embed formatting key in
# gamedaybot.discord_bot.formatting. get_trophies is absent deliberately -- it
# renders as discrete embed fields rather than one code block.
FUNCTION_TO_EMBED_KEY = {
    "get_matchups": "matchups",
    "get_monitor": "monitor",
    "get_scoreboard_short": "scoreboard",
    "get_projected_scoreboard": "scoreboard",
    "get_close_scores": "close_scores",
    "get_power_rankings": "power_rankings",
    "get_standings": "standings",
    "get_waiver_report": "waiver_report",
    "get_final": "final",
}


def _build_league(data):
    """Private leagues need the ESPN_S2/SWID cookie pair; public ones don't."""
    if data['swid'] == NO_SWID or data['espn_s2'] == NO_ESPN_S2:
        return League(league_id=data['league_id'], year=data['year'])
    return League(league_id=data['league_id'], year=data['year'],
                  espn_s2=data['espn_s2'], swid=data['swid'])


def _report_text(function, league, data):
    """Returns the report body, or None if there is nothing to post."""
    if function == "get_matchups":
        return espn.get_matchups(league) + "\n\n" + espn.get_projected_scoreboard(league)
    if function == "get_monitor":
        return espn.get_monitor(league)
    if function == "get_scoreboard_short":
        return espn.get_scoreboard_short(league) + "\n\n" + espn.get_projected_scoreboard(league)
    if function == "get_projected_scoreboard":
        return espn.get_projected_scoreboard(league)
    if function == "get_close_scores":
        return espn.get_close_scores(league)
    if function == "get_power_rankings":
        return espn.get_power_rankings(league)
    if function == "get_trophies":
        return espn.get_trophies(league)
    if function == "get_standings":
        return espn.get_standings(league, data['top_half_scoring'])
    if function == "get_final":
        # Runs Tuesday, so it reports the week that just finished.
        week = league.current_week - 1
        return ("Final " + espn.get_scoreboard_short(league, week=week) + "\n\n"
                + espn.get_trophies(league, week=week))
    if function == "get_waiver_report":
        if data['swid'] == NO_SWID or data['espn_s2'] == NO_ESPN_S2:
            logger.warning("Waiver report needs ESPN_S2/SWID (ESPN treats "
                           "transactions as private) -- skipping")
            return None
        return espn.get_waiver_report(league, league.settings.faab)

    logger.error("Unknown report: %s", function)
    return None


def check_trades(discord_bot, league):
    """Collect trade activity and announce each trade never seen before."""
    new_rows = collector.collect_trades(league)
    if not new_rows:
        return

    cutoff_ms = (time.time() - TRADE_ANNOUNCE_WINDOW_SECONDS) * 1000
    trades = {}
    for row in new_rows:
        trades.setdefault(row["trade_date"], []).append(row)

    for trade_date in sorted(trades):
        if trade_date < cutoff_ms:
            logger.info("Seeding old trade from %s without announcing", trade_date)
            continue
        discord_bot.send_message(
            embed=discord_fmt.trade_embed(trades[trade_date], league=league))


def send_matchup_polls(discord_bot, league, duration_hours, week=None):
    """
    Post one "who wins?" poll per matchup. Teams on a bye (playoff weeks)
    have no opponent and get no poll. Returns the number of polls posted.
    """
    if week is None:
        week = league.current_week
    posted = 0
    for box in league.box_scores(week=week):
        if not box.away_team or not box.home_team:
            continue
        poll = discord_fmt.matchup_poll(
            box.home_team.team_name, box.away_team.team_name,
            duration_hours, week=week)
        discord_bot.send_poll(poll)
        posted += 1
    if posted == 0:
        logger.info("No matchups to poll for week %s", week)
    return posted


def _send_init(discord_bot, data, league=None):
    if data.get('init_webhook_urls'):
        discord_bot = Discord(data['init_webhook_urls'])
    init_msg = data.get('init_msg')
    if init_msg:
        # INIT_MSG replaces the generated summary outright.
        discord_bot.send_message(text=init_msg)
        return
    discord_bot.send_message(embed=discord_fmt.init_embed(data, league=league))


def espn_bot(function):
    """
    Generate one report and post it to every URL in DISCORD_WEBHOOK_URL.

    Parameters
    ----------
    function: str
        Which report to send. One of:

        get_matchups              the week's matchups plus projected scores
        send_matchup_polls        one who-wins poll per matchup (see MATCHUP_POLLS)
        get_monitor               injured/questionable starters to watch
        get_scoreboard_short      current scores plus projected scores
        get_projected_scoreboard  projected scores only
        get_close_scores          games projected to finish within 15 points
        get_power_rankings        power rankings with week-over-week movement
        get_standings             current standings
        get_trophies              this week's trophies
        get_final                 last week's final scores and trophies
        get_waiver_report         today's waiver moves (private leagues only)
        check_trades              announce trades not seen before (private leagues only)
        collect_snapshot          persist the week to SQLite; posts nothing
        collect_players           persist player pool, teams, schedule, and draft picks; posts nothing
        init                      startup confirmation message
    """
    data = get_env_vars()
    discord_bot = Discord(data['discord_webhook_urls'])
    # Built before the init branch on purpose: the startup message claims the
    # league connected, so it has to have actually connected.
    league = _build_league(data)

    if function == "init":
        # Exempt from the season check -- the startup confirmation is worth
        # sending in the offseason too. The player pool is collected here so
        # the draft board has data before START_DATE, when no other job fires.
        _send_init(discord_bot, data, league=league)
        try:
            collector.collect_league_state(league)
        except Exception as e:
            logger.warning("League-state collect on init failed: %s", e)
        return

    if function == "collect_players":
        collector.collect_league_state(league)
        return

    # Against the last scoring period, not the number of matchup periods:
    # the two-week playoff rounds make 18 weeks out of 16 matchups, and the
    # shorter bound would silently skip the final three Tuesday snapshots.
    # One period of grace, because ESPN has moved on to the next scoring
    # period by the time the Tuesday jobs fire -- the final week's snapshot
    # and its Discord report happen after the season's last period.
    # END_DATE keeps the scheduler from running anything past that.
    if league.scoringPeriodId > collector.last_scoring_period(league) + 1:
        logger.info("Not in active season")
        return

    logger.info("Report: %s", function)

    if function == "collect_snapshot":
        collector.collect_weekly_snapshot(league)
        return

    if function == "send_matchup_polls":
        send_matchup_polls(discord_bot, league, data['matchup_poll_hours'])
        return

    if function == "check_trades":
        if data['swid'] == NO_SWID or data['espn_s2'] == NO_ESPN_S2:
            logger.warning("Trade check needs ESPN_S2/SWID (ESPN treats "
                           "transactions as private) -- skipping")
            return
        check_trades(discord_bot, league)
        return

    text = _report_text(function, league, data)
    if not text:
        return

    if function == "get_trophies":
        discord_bot.send_message(embed=discord_fmt.trophies_embed(text, league=league))
    else:
        discord_bot.send_message(embed=discord_fmt.code_block_embed(
            FUNCTION_TO_EMBED_KEY[function], text, league=league))
