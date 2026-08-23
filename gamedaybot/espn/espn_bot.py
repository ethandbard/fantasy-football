"""
Generates a named report and posts it to the Discord webhook.

The scheduler drives this. The slash-command bot in gamedaybot.discord_bot.bot
serves the same reports over its gateway connection, calling the same
functionality.py helpers and the same embed builders so the two stay identical.
"""
import logging

from espn_api.football import League

import gamedaybot.discord_bot.formatting as discord_fmt
import gamedaybot.espn.collector as collector
import gamedaybot.espn.functionality as espn
from gamedaybot.discord_bot.webhook import Discord
from gamedaybot.espn.env_vars import NO_ESPN_S2, NO_SWID, get_env_vars

logger = logging.getLogger(__name__)

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


def _send_init(discord_bot, data):
    init_msg = data.get('init_msg')
    if init_msg:
        # INIT_MSG replaces the generated summary outright.
        discord_bot.send_message(text=init_msg)
        return
    discord_bot.send_message(embed=discord_fmt.init_embed(data))


def espn_bot(function):
    """
    Generate one report and post it to DISCORD_WEBHOOK_URL.

    Parameters
    ----------
    function: str
        Which report to send. One of:

        get_matchups              the week's matchups plus projected scores
        get_monitor               injured/questionable starters to watch
        get_scoreboard_short      current scores plus projected scores
        get_projected_scoreboard  projected scores only
        get_close_scores          games projected to finish within 15 points
        get_power_rankings        power rankings with week-over-week movement
        get_standings             current standings
        get_trophies              this week's trophies
        get_final                 last week's final scores and trophies
        get_waiver_report         today's waiver moves (private leagues only)
        collect_snapshot          persist the week to SQLite; posts nothing
        collect_players           persist the ESPN player pool; posts nothing
        init                      startup confirmation message
    """
    data = get_env_vars()
    discord_bot = Discord(data['discord_webhook_url'])
    # Built before the init branch on purpose: the startup message claims the
    # league connected, so it has to have actually connected.
    league = _build_league(data)

    if function == "init":
        # Exempt from the season check -- the startup confirmation is worth
        # sending in the offseason too. The player pool is collected here so
        # the draft board has data before START_DATE, when no other job fires.
        _send_init(discord_bot, data)
        try:
            collector.collect_player_pool(league)
        except Exception as e:
            logger.warning("Player pool collect on init failed: %s", e)
        return

    if function == "collect_players":
        collector.collect_player_pool(league)
        return

    if league.scoringPeriodId > len(league.settings.matchup_periods):
        logger.info("Not in active season")
        return

    logger.info("Report: %s", function)

    if function == "collect_snapshot":
        collector.collect_weekly_snapshot(league)
        return

    text = _report_text(function, league, data)
    if not text:
        return

    if function == "get_trophies":
        discord_bot.send_message(embed=discord_fmt.trophies_embed(text, league=league))
    else:
        discord_bot.send_message(embed=discord_fmt.code_block_embed(
            FUNCTION_TO_EMBED_KEY[function], text, league=league))
