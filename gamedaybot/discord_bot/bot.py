"""
Discord slash-command bot: lets league members ask for standings/matchups/etc
on demand instead of only receiving scheduled pushes. Runs as a persistent
gateway connection (separate from the DISCORD_WEBHOOK_URL used for scheduled
messages), reusing the same espn_api League + functionality.py formatting
that the scheduler already uses.
"""
import logging

import discord
from discord import app_commands
from espn_api.football import League

import gamedaybot.espn.functionality as espn
import gamedaybot.discord_bot.formatting as fmt
from gamedaybot.espn.env_vars import get_env_vars

logger = logging.getLogger(__name__)

DASHBOARD_MESSAGE = (
    "View the full dashboard (charts, standings, season recap) here: "
    "{url}"
)


def _get_league():
    data = get_env_vars()
    swid = data['swid']
    espn_s2 = data['espn_s2']
    if swid == '{1}' or espn_s2 == '1':
        return League(league_id=data['league_id'], year=int(data['year'])), data
    return League(
        league_id=data['league_id'], year=int(data['year']),
        espn_s2=espn_s2, swid=swid,
    ), data


def _friendly_error_message():
    return (
        "Couldn't get that right now -- this usually means the season hasn't "
        "started yet (no draft/rosters live) or ESPN's API is temporarily "
        "unavailable. Try again once the season is underway."
    )


def build_bot(dashboard_url):
    intents = discord.Intents.default()
    bot = discord.Client(intents=intents)
    tree = app_commands.CommandTree(bot)

    @bot.event
    async def on_ready():
        logger.info("Discord bot logged in as %s", bot.user)
        for guild in bot.guilds:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            logger.info("Synced slash commands to guild %s", guild.name)

    def _register(name, description, embed_key, fn):
        @tree.command(name=name, description=description)
        async def _cmd(interaction: discord.Interaction):
            await interaction.response.defer()
            try:
                league, _ = _get_league()
                text = fn(league)
            except Exception:
                logger.exception("Error handling /%s", name)
                await interaction.followup.send(_friendly_error_message())
                return
            payload = fmt.code_block_embed(embed_key, text, league=league)
            await interaction.followup.send(embed=discord.Embed.from_dict(payload))

    _register("matchups", "Current week's matchups", "matchups", espn.get_matchups)
    _register("scoreboard", "Current scoreboard", "scoreboard", espn.get_scoreboard_short)
    _register("standings", "Current standings", "standings",
               lambda league: espn.get_standings(league))
    _register("power-rankings", "Current power rankings", "power_rankings",
               espn.get_power_rankings)
    _register("monitor", "Injury/player monitor report", "monitor", espn.get_monitor)

    @tree.command(name="trophies", description="This week's trophies")
    async def trophies(interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            league, _ = _get_league()
            text = espn.get_trophies(league)
        except Exception:
            logger.exception("Error handling /trophies")
            await interaction.followup.send(_friendly_error_message())
            return
        payload = fmt.trophies_embed(text, league=league)
        await interaction.followup.send(embed=discord.Embed.from_dict(payload))

    @tree.command(name="waiver-report", description="Waiver report (private leagues only)")
    async def waiver_report(interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            league, data = _get_league()
            if data['swid'] == '{1}' or data['espn_s2'] == '1':
                await interaction.followup.send(
                    "Waiver report requires a private league with ESPN_S2/SWID configured.")
                return
            text = espn.get_waiver_report(league, league.settings.faab)
        except Exception:
            logger.exception("Error handling /waiver-report")
            await interaction.followup.send(_friendly_error_message())
            return
        payload = fmt.code_block_embed("waiver_report", text, league=league)
        await interaction.followup.send(embed=discord.Embed.from_dict(payload))

    @tree.command(name="dashboard", description="Link to the web dashboard")
    async def dashboard(interaction: discord.Interaction):
        embed = discord.Embed(
            title="📈 Fantasy Dashboard",
            description=DASHBOARD_MESSAGE.format(url=dashboard_url),
            color=fmt.EMBED_COLORS["dashboard"],
        )
        await interaction.response.send_message(embed=embed)

    return bot


def run(token, dashboard_url):
    bot = build_bot(dashboard_url)
    bot.run(token, log_handler=None)
