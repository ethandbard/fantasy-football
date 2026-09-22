"""
Runtime configuration for the agent service, read once from the environment.

Shares config.env with the bot, so LEAGUE_ID, LEAGUE_YEAR, ESPN_S2, SWID, and
DB_PATH mean the same thing here. Everything agent-specific is prefixed
AGENT_ and documented in README.md.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gamedaybot.espn.env_vars import NO_ESPN_S2, NO_SWID


def _bool(value, default=False):
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "t", "yes", "y")


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class AgentConfig:
    league_id: int
    year: int
    espn_s2: str
    swid: str
    team_id: int
    timezone: str
    data_dir: Path
    repo_agents_dir: Path
    webhook_url: Optional[str]
    owner_discord_id: Optional[str]
    port: int
    heavy_model: str
    light_model: str
    dry_run: bool
    ask_daily_limit: int
    ask_league_daily_limit: int
    heavy_search_cap: int
    light_search_cap: int
    heavy_max_turns: int
    light_max_turns: int
    ask_expiry_hours: int
    enabled_schedule: bool
    # Whether every /ask gets the managing agent's record (briefs, research,
    # state, season log, activity), or only the owner does.
    ask_share_record: bool

    @property
    def has_cookies(self):
        return self.espn_s2 not in (None, "", NO_ESPN_S2) and self.swid not in (None, "", NO_SWID)

    @property
    def has_claude_auth(self):
        return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def rules_dir(self):
        return self.data_dir

    def ensure_dirs(self):
        for sub in ("research", "state", "runs"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


def from_env():
    # Writes must come from the account that owns TEAM_ID. The bot's cookies
    # only need to belong to any league member, so the agent accepts its own
    # pair and falls back to the bot's.
    swid = os.environ.get("AGENT_SWID") or os.environ.get("SWID", NO_SWID)
    espn_s2 = os.environ.get("AGENT_ESPN_S2") or os.environ.get("ESPN_S2", NO_ESPN_S2)
    if not swid.startswith("{"):
        swid = "{" + swid
    if not swid.endswith("}"):
        swid = swid + "}"
    db_path = os.environ.get("DB_PATH", "/app/data/fantasy.db")
    data_dir = Path(os.environ.get("AGENT_DATA_DIR") or (Path(db_path).parent / "agent"))
    here = Path(__file__).resolve().parent.parent
    return AgentConfig(
        league_id=int(os.environ["LEAGUE_ID"]),
        year=int(os.environ.get("LEAGUE_YEAR", 2026)),
        espn_s2=espn_s2,
        swid=swid,
        team_id=_int(os.environ.get("TEAM_ID"), 11),
        timezone=os.environ.get("TIMEZONE", "America/New_York"),
        data_dir=data_dir,
        repo_agents_dir=Path(os.environ.get("AGENT_REPO_DIR") or (here / "fantasy-football-agents")),
        webhook_url=os.environ.get("AGENT_WEBHOOK_URL") or None,
        owner_discord_id=os.environ.get("OWNER_DISCORD_ID") or None,
        port=_int(os.environ.get("AGENT_PORT"), 8010),
        heavy_model=os.environ.get("AGENT_MODEL_HEAVY", "opus"),
        light_model=os.environ.get("AGENT_MODEL_LIGHT", "sonnet"),
        dry_run=_bool(os.environ.get("AGENT_DRY_RUN"), False),
        ask_daily_limit=_int(os.environ.get("AGENT_ASK_DAILY_LIMIT"), 3),
        ask_league_daily_limit=_int(os.environ.get("AGENT_ASK_LEAGUE_DAILY_LIMIT"), 20),
        heavy_search_cap=_int(os.environ.get("AGENT_HEAVY_SEARCH_CAP"), 40),
        light_search_cap=_int(os.environ.get("AGENT_LIGHT_SEARCH_CAP"), 8),
        heavy_max_turns=_int(os.environ.get("AGENT_HEAVY_MAX_TURNS"), 200),
        light_max_turns=_int(os.environ.get("AGENT_LIGHT_MAX_TURNS"), 60),
        ask_expiry_hours=_int(os.environ.get("AGENT_ASK_EXPIRY_HOURS"), 24),
        enabled_schedule=_bool(os.environ.get("AGENT_SCHEDULE"), True),
        ask_share_record=_bool(os.environ.get("AGENT_ASK_SHARE_RECORD"), True),
    )
