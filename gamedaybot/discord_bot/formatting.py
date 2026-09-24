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
    "trade": 0xE91E63,           # magenta
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
    "trade": "🔄 Trade Alert",
    "final": "🏈 Final Scores",
}


# Discord's limits for a poll create request object.
POLL_QUESTION_MAX = 300
POLL_ANSWER_MAX = 55
POLL_HOURS_MIN = 1
POLL_HOURS_MAX = 768  # 32 days


def _clip(text, limit):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def matchup_poll(home_name, away_name, duration_hours, week=None):
    """
    A Discord poll create request object asking who wins one matchup, one
    answer per team. Usable directly in a webhook's "poll" field
    (https://discord.com/developers/docs/resources/poll#poll-create-request-object).
    """
    question = f"{home_name} vs {away_name}"
    if week:
        question = f"Week {week}: {question}"
    hours = max(POLL_HOURS_MIN, min(int(duration_hours), POLL_HOURS_MAX))
    return {
        "question": {"text": _clip(question, POLL_QUESTION_MAX)},
        "answers": [
            {"poll_media": {"text": _clip(home_name, POLL_ANSWER_MAX)}},
            {"poll_media": {"text": _clip(away_name, POLL_ANSWER_MAX)}},
        ],
        "duration": hours,
        "allow_multiselect": False,
        "layout_type": 1,
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


def trade_embed(rows, league=None):
    """
    One completed trade as an embed: a field per receiving team listing the
    players it got. `rows` is one trade's player rows from the trades table
    (they share a trade_date); teams appear in first-seen order.
    """
    sides = {}
    for r in rows:
        team = r.get("to_team_name") or "Unknown team"
        sides.setdefault(team, []).append(r)

    fields = []
    for team, players in sides.items():
        lines = []
        for p in players:
            pos = p.get("position")
            name = p.get("player_name") or "Unknown player"
            lines.append(f"{pos} {name}" if pos else name)
        fields.append({
            "name": f"📥 {team} receives",
            "value": "\n".join(lines),
            "inline": True,
        })

    payload = {
        "title": TITLES["trade"],
        "description": " ⇄ ".join(sides) if len(sides) > 1 else None,
        "color": EMBED_COLORS["trade"],
        "fields": fields,
        "timestamp": _now_iso(),
    }
    if payload["description"] is None:
        del payload["description"]
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


# ---------------------------------------------------------------------------
# The agent's schedule, as people read it
# ---------------------------------------------------------------------------

# Minute-level housekeeping. It always "fires next", so listing it pushes
# every job anyone is waiting for off the bottom of the list.
HOUSEKEEPING_JOBS = {"tick", "poll_offers", "expire_asks", "remind_asks"}

# Scheduler ids that do not say what they are. "preview" is the Monday note,
# not the dashboard's matchup preview, which has caught people out.
JOB_LABELS = {
    "pregame": "pre-game lineup check",
    "preview": "week-ahead note to Discord",
    "preview_site": "matchup preview for the dashboard",
    "recap": "league recap for the dashboard",
    "power": "power rankings for the dashboard",
    "research": "weekly research",
    "plan": "roster plan",
    "ensure_wakeups": "re-plan the pre-game checks",
    "postwaiver": "post-waiver adjustment",
    "designations": "Friday injury designations",
    "canary": "ESPN login check",
}


def _eastern():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001 - no tz database on the host
        from datetime import timedelta
        return timezone(timedelta(hours=-4))


def _when(raw):
    """An ISO timestamp as an aware datetime; one with no offset is UTC,
    which is how the agent stores them."""
    at = datetime.fromisoformat(str(raw))
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def schedule_lines(wakeups=None, fires=None, limit=14):
    """
    The agent's planned wakeups and the scheduler's next fixed jobs as one
    list: Eastern time, soonest first, housekeeping left out.

    The two arrive in different shapes and different zones -- wakeups as
    {"run_at", "job", "label"} in UTC, fixed jobs as {"next", "job"} in UTC or
    Eastern depending on the trigger -- and printing them as they come makes
    a list that is neither readable nor in order.
    """
    rows = []
    for w in wakeups or []:
        rows.append((_when(w["run_at"]), w["job"], w.get("label") or ""))
    for f in fires or []:
        if f["job"] not in HOUSEKEEPING_JOBS:
            rows.append((_when(f["next"]), f["job"], ""))

    lines = []
    for at, job, label in sorted(rows, key=lambda r: r[0])[:limit]:
        stamp = at.astimezone(_eastern()).strftime("%a %b %d, %I:%M %p ET").replace(", 0", ", ")
        what = JOB_LABELS.get(job, job)
        # A pre-game label repeats the date the line already opens with.
        label = label.split(" ", 3)[-1].lstrip("0") if label.endswith("ET kickoff") else label
        lines.append(f"• {stamp} · {what}" + (f" ({label})" if label else ""))
    return lines
