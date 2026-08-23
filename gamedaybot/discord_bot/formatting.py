"""
Shared Discord embed formatting, used by both the slash-command bot
(gamedaybot.discord_bot.bot, via the live gateway) and the scheduled webhook
sender (gamedaybot.discord_bot.webhook, via espn_bot.py's dispatch). Keeping
this in one place means a scheduled standings post and a manual /standings
command look identical.

Payloads here are plain JSON-serializable dicts matching Discord's embed
object shape (https://discord.com/developers/docs/resources/message#embed-object)
-- usable directly in a webhook's "embeds" field, or passed through
discord.Embed.from_dict() for the gateway bot.
"""
from datetime import datetime, timezone

EMBED_COLORS = {
    "matchups": 0x3498DB,        # blue
    "scoreboard": 0x2ECC71,      # green
    "close_scores": 0x2ECC71,    # green
    "standings": 0xF1C40F,       # gold
    "power_rankings": 0x9B59B6,  # purple
    "trophies": 0xE67E22,        # orange
    "monitor": 0xE74C3C,         # red
    "waiver_report": 0x1ABC9C,   # teal
    "final": 0x2ECC71,           # green
    "init": 0x3498DB,            # blue
    "dashboard": 0x3498DB,       # blue
}

TITLES = {
    "matchups": "📅 Matchups",
    "scoreboard": "🏈 Scoreboard",
    "close_scores": "🔥 Close Scores",
    "standings": "📊 Standings",
    "power_rankings": "💪 Power Rankings",
    "trophies": "🏆 Trophies",
    "monitor": "🏥 Player Monitor",
    "waiver_report": "🔁 Waiver Report",
    "final": "🏈 Final Scores",
}


def _footer_for(league):
    if league is None:
        return None
    return {"text": f"{league.settings.name} • {league.year}"}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def code_block_embed(key, text, league=None, strip_header=True):
    """
    Wraps plain-text output (already column-aligned by functionality.py) in a
    code block inside an embed, dropping the first line when it's just a
    redundant restatement of the title (e.g. "Matchups", "Current Standings").
    """
    lines = text.split("\n")
    body = "\n".join(lines[1:]) if strip_header and len(lines) > 1 else text
    body = body.strip() or "No data."
    payload = {
        "title": TITLES.get(key, key),
        "description": f"```{body[:3900]}```",
        "color": EMBED_COLORS.get(key, 0x99AAB5),
        "timestamp": _now_iso(),
    }
    footer = _footer_for(league)
    if footer:
        payload["footer"] = footer
    return payload


def trophies_embed(text, league=None):
    """
    get_trophies() returns alternating [emoji title, detail] line pairs after
    a header line -- render each pair as its own embed field instead of one
    monospace block, since these are conceptually separate "cards".
    """
    lines = [ln for ln in text.split("\n") if ln.strip()]
    body_lines = lines[1:] if len(lines) > 1 else []
    fields = [
        {"name": body_lines[i], "value": body_lines[i + 1], "inline": False}
        for i in range(0, len(body_lines) - 1, 2)
    ]
    payload = {
        "title": TITLES["trophies"],
        "color": EMBED_COLORS["trophies"],
        "timestamp": _now_iso(),
    }
    if fields:
        payload["fields"] = fields
    else:
        payload["description"] = "No trophies yet -- check back after week 1."
    footer = _footer_for(league)
    if footer:
        payload["footer"] = footer
    return payload


def init_embed(data, league=None):
    """
    Startup summary, built from the resolved env config so it reports what the
    bot will actually do rather than a hardcoded guess.

    `data` is the dict returned by gamedaybot.espn.env_vars.get_env_vars().
    Year prefers the connected ESPN league, then LEAGUE_YEAR, so the
    message cannot drift from the season the process actually opened.
    """
    year = league.year if league is not None else data["year"]
    league_name = None
    if league is not None:
        league_name = getattr(getattr(league, "settings", None), "name", None)
    lines = [
        "✅ **Connected to league successfully!**",
    ]
    if league_name:
        lines.append(f"🏈 **League:** {league_name}")
    lines.extend([
        f"📅 **Season:** {year} season ({data['ff_start_date']} – {data['ff_end_date']})",
        f"⏰ **Timezone:** {data['my_timezone']}",
        f"📊 **Waiver reports:** {'Every day' if data['daily_waiver'] else 'Wednesdays only'}",
        f"🏥 **Player monitoring:** {'Enabled' if data['monitor_report'] else 'Disabled'}",
        "",
        "🎯 **Bot is now running and will send automatic updates on schedule!**",
        f"🔥 **Ready for the {year} fantasy season!**",
    ])
    return {
        "title": "🤖 Fantasy Football Bot Started!",
        "description": "\n".join(lines),
        "color": EMBED_COLORS["init"],
        "timestamp": _now_iso(),
    }
