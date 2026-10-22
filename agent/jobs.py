"""
The job catalogue: what each run is for, which tools it gets, which model,
and how many turns and searches it may spend.

One job trades: the general manager (`gm`). It has one identity (gm.md is
its system prompt), one memory (the strategy file, the negotiation ledger,
the scorecard), and every tool. Each run gets a short *situation* brief
from agent/prompts/situations instead of a job of its own: weekly,
trade_desk, after_games, offer, integrity, or manual. The old research,
plan, and trade_review jobs are aliases for those situations, so the
Discord commands and the HTTP routes keep working.

Workers are the cheap runs that never trade: the pre-game check, the
Friday designations, the post-waiver reconcile, the wire claim, the data
analyst, the league analyst, and the dashboard prose. persona.md is their
shared system prompt; RULES.md is appended for the owner's jobs.
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

import gamedaybot.espn.roster as roster
from agent import store

PROMPTS = Path(__file__).resolve().parent / "prompts"
SITUATIONS = ("weekly", "trade_desk", "after_games", "offer", "integrity", "manual")
DEFAULT_SITUATION = "manual"


@dataclass
class JobSpec:
    name: str
    title: str
    kind: str                      # Discord color key
    model: str                     # "heavy", "light", or "gm"
    tool_groups: List[str]
    web: bool
    writes: bool
    max_turns_key: str             # "heavy" or "light"
    search_cap_key: str            # "heavy", "light", or "gm"
    prompt_file: str
    effort: str = "medium"
    owner_job: bool = True         # gets RULES.md and private state
    post_brief: bool = True
    persona_file: str = "persona.md"
    subagents: bool = False        # may delegate scouting to the scout subagent


JOBS = {
    # The general manager: the one run that trades. Its prompt file is the
    # situation brief; gm.md is its system prompt.
    "gm": JobSpec(
        "gm", "General manager", "trade", "gm",
        ["read", "write", "state", "evaluate", "negotiate", "usage"], web=True, writes=True,
        max_turns_key="heavy", search_cap_key="gm", prompt_file="situations/manual.md", effort="high",
        persona_file="gm.md", subagents=True),
    # The in-house data analyst: its own model from usage data, no web, so it
    # sees what the consensus does not. Runs before the weekly session, which reads it.
    "analytics": JobSpec(
        "analytics", "Data analyst report", "analytics", "heavy",
        ["read", "analytics"], web=False, writes=False, max_turns_key="light", search_cap_key="light",
        prompt_file="analytics.md", effort="high"),
    "postwaiver": JobSpec(
        "postwaiver", "Post-waiver adjust", "brief", "light",
        ["read", "write", "state"], web=True, writes=True, max_turns_key="light", search_cap_key="light",
        prompt_file="postwaiver.md"),
    "designations": JobSpec(
        "designations", "Friday designations", "brief", "light",
        ["read", "write", "state"], web=True, writes=True, max_turns_key="light", search_cap_key="light",
        prompt_file="designations.md"),
    "pregame": JobSpec(
        "pregame", "Pre-game check", "pregame", "light",
        ["read", "write", "state"], web=True, writes=True, max_turns_key="light", search_cap_key="light",
        prompt_file="pregame.md"),
    "lineup": JobSpec(
        "lineup", "Lineup check", "pregame", "light",
        ["read", "write", "state"], web=True, writes=True, max_turns_key="light", search_cap_key="light",
        prompt_file="lineup.md"),
    # Started by the nightly wire watch only when another team dropped someone.
    "wire": JobSpec(
        "wire", "Waiver wire watch", "brief", "light",
        ["read", "write", "state"], web=True, writes=True, max_turns_key="light", search_cap_key="light",
        prompt_file="wire.md"),
    "ask": JobSpec(
        "ask", "League analyst", "answer", "light",
        ["analyst"], web=True, writes=False, max_turns_key="light", search_cap_key="light",
        prompt_file="analyst.md", owner_job=False, post_brief=False),
    # League-facing prose for the dashboard. Public tools only, no web, no
    # Discord brief: the site_content row is the output.
    "recap": JobSpec(
        "recap", "League recap", "brief", "light",
        ["analyst", "site"], web=False, writes=False, max_turns_key="light", search_cap_key="light",
        prompt_file="recap.md", owner_job=False, post_brief=False),
    "preview": JobSpec(
        "preview", "Matchup preview", "brief", "light",
        ["analyst", "site"], web=False, writes=False, max_turns_key="light", search_cap_key="light",
        prompt_file="preview.md", owner_job=False, post_brief=False),
    "power": JobSpec(
        "power", "Power rankings", "brief", "light",
        ["analyst", "site"], web=False, writes=False, max_turns_key="light", search_cap_key="light",
        prompt_file="power.md", owner_job=False, post_brief=False),
}

# Old job names, kept for the Discord commands and the HTTP routes.
ALIASES = {
    "research": ("gm", {"situation": "weekly"}),
    "plan": ("gm", {"situation": "weekly"}),
    "trade_review": ("gm", {"situation": "offer"}),
}

SITUATION_TITLES = {
    "weekly": "Weekly session", "trade_desk": "Trade desk", "after_games": "After the games",
    "offer": "Offer review", "integrity": "Roster integrity", "manual": "General manager",
}


def resolve(name, params=None):
    """(job, params) with aliases applied. Alias params fill in only what the caller left unset."""
    params = dict(params or {})
    if name in ALIASES:
        name, defaults = ALIASES[name]
        for k, v in defaults.items():
            params.setdefault(k, v)
    if name == "gm":
        situation = str(params.get("situation") or DEFAULT_SITUATION)
        if situation not in SITUATIONS:
            raise KeyError(f"unknown situation {situation}; known: {', '.join(SITUATIONS)}")
        params["situation"] = situation
    return name, params


def get(name):
    if name in ALIASES:
        name = ALIASES[name][0]
    if name not in JOBS:
        raise KeyError(f"unknown job {name}; known: {', '.join(JOBS)}")
    return JOBS[name]


def title(spec, params=None):
    if spec.name == "gm":
        return SITUATION_TITLES.get((params or {}).get("situation") or DEFAULT_SITUATION, spec.title)
    return spec.title


def search_cap(cfg, spec):
    if spec.search_cap_key == "gm":
        return cfg.gm_search_cap
    return cfg.heavy_search_cap if spec.search_cap_key == "heavy" else cfg.light_search_cap


def model_name(cfg, spec):
    if spec.model == "gm":
        return cfg.gm_model
    return cfg.heavy_model if spec.model == "heavy" else cfg.light_model


def read_prompt(filename):
    return (PROMPTS / filename).read_text(encoding="utf-8")


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template, **values):
    return template.format_map(_Safe(values))


# Rendered into the analyst prompt when the managing agent's record is open
# to the person asking: always for the owner, and for everyone while
# AGENT_ASK_SHARE_RECORD is on. Otherwise the prompt gets "" and the public
# tools only.
_RECORD_TOOLS = (
    "read_briefs (the agent's recent briefs, offer reviews included), read_season_log, read_research, "
    "read_state, and get_rules. Use them for anything about that agent's decisions, research, or plans. "
    "Briefs and the season log are snapshots of the moment they were written; get_agent_activity says "
    "what happened since: which asks were approved, sent, rejected, or expired, and whether proposals "
    "the agent sent were accepted or declined. Check it before saying anything is still pending. "
)
OWNER_NOTE = (
    "The asker owns the agent that manages team {owner_team_id}, so its private record is open to them: "
    + _RECORD_TOOLS +
    "The rules below about not revealing that agent's reasoning do not apply to this asker."
)
LEAGUE_NOTE = (
    "The league has opened the record of the agent that manages team {owner_team_id} to every member, "
    "including this asker: " + _RECORD_TOOLS +
    "The rules below about not revealing that agent's reasoning do not apply while the record is open. "
    "What the agent wrote about other managers is its own guesswork from public data, and you say so "
    "when you repeat it."
)


def is_owner(cfg, params):
    """Whether the ask params name the managed team as the asker's."""
    try:
        return int((params or {}).get("asker_team_id")) == int(cfg.team_id)
    except (TypeError, ValueError):
        return False


def record_open(cfg, params):
    """Whether this ask gets the managing agent's record: the owner always, everyone when sharing is on."""
    return is_owner(cfg, params) or bool(getattr(cfg, "ask_share_record", False))


def record_note(cfg, params):
    """The note the analyst prompt carries about the record, or "" when it stays closed."""
    if is_owner(cfg, params):
        return OWNER_NOTE.format(owner_team_id=cfg.team_id)
    if record_open(cfg, params):
        return LEAGUE_NOTE.format(owner_team_id=cfg.team_id)
    return ""


# Appended to every owner job's system prompt. The runner's audit checks the
# brief carries this section.
STEPS_SKIPPED = (
    'End the brief with a section titled "Steps skipped", after the '
    "disagreements section: each numbered step of the job you did not do, or "
    "did only in part, and why, one line each. Write \"none\" when you did "
    "every step. A step whose condition did not apply (no open bench spot, no "
    "game-time decision) counts as done. The section does not count toward the "
    "brief's length limit."
)


def system_prompt(spec, cfg, rules_prose):
    persona = read_prompt(spec.persona_file)
    now = datetime.now(roster._eastern())
    body = render(persona, team_id=cfg.team_id, now=now.strftime("%A %B %d, %Y %I:%M %p ET"),
                  writes="enabled" if not cfg.dry_run else "DISABLED (dry run: previews only, nothing posts)")
    if spec.owner_job:
        body += "\n\n# Steps skipped\n\n" + STEPS_SKIPPED
        body += "\n\n# Rules\n\n" + rules_prose
    return body


def inbox_text(mark=True):
    """The workers' notes for the general manager since its last run, as prompt text."""
    try:
        rows = store.unread_inbox(mark=mark)
    except Exception:
        return "(inbox unavailable)"
    if not rows:
        return "(none)"
    return "\n".join(f"- {r['at'][:16]} [{r['source']}] {r['text']}" for r in rows)


def user_prompt(spec, ctx, params):
    params = params or {}
    if spec.name == "gm":
        situation = params.get("situation") or DEFAULT_SITUATION
        template = read_prompt(f"situations/{situation}.md")
    else:
        template = read_prompt(spec.prompt_file)
    now = datetime.now(roster._eastern())
    cfg = ctx.cfg
    values = {
        "week": ctx.week, "scoring_period": ctx.scoring_period,
        # ESPN rolls its current week on Tuesday morning, so "the week just
        # played" is one behind unless the caller says which.
        "played_week": int(params.get("week") or max(1, ctx.week - 1)),
        "now": now.strftime("%A %B %d, %Y %I:%M %p ET"),
        "team": ctx.team_name(cfg.team_id), "team_id": cfg.team_id, "owner_team_id": cfg.team_id,
        "search_cap": search_cap(cfg, spec),
        "owner_note": "", "findings": "", "reason": "", "trade": "",
    }
    values.update({k: v for k, v in params.items() if isinstance(v, (str, int, float))})
    if "players" in params:
        values["players"] = ", ".join(params["players"])
    if spec.name == "gm":
        values["inbox"] = inbox_text()
    return render(template, **values)
