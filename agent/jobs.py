"""
The job catalogue: what each run is for, which tools it gets, which model,
and how many turns and searches it may spend.

Prompts live in agent/prompts as markdown so they can be read and edited as
text. persona.md is the shared system prompt; RULES.md is appended for the
owner's jobs; each job's file is the user prompt for that run.
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

import gamedaybot.espn.roster as roster

PROMPTS = Path(__file__).resolve().parent / "prompts"


@dataclass
class JobSpec:
    name: str
    title: str
    kind: str                      # Discord color key
    model: str                     # "heavy" or "light"
    tool_groups: List[str]
    web: bool
    writes: bool
    max_turns_key: str             # "heavy" or "light"
    search_cap_key: str            # "heavy" or "light"
    prompt_file: str
    effort: str = "medium"
    owner_job: bool = True         # gets RULES.md and private state
    post_brief: bool = True


JOBS = {
    "research": JobSpec(
        "research", "Week review and research", "brief", "heavy",
        ["read", "state"], web=True, writes=False, max_turns_key="heavy", search_cap_key="heavy",
        prompt_file="research.md", effort="high"),
    "plan": JobSpec(
        "plan", "Roster plan", "brief", "heavy",
        ["read", "write", "state"], web=True, writes=True, max_turns_key="heavy", search_cap_key="light",
        prompt_file="plan.md", effort="high"),
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
    "trade_review": JobSpec(
        "trade_review", "Trade review", "trade", "heavy",
        ["read", "write", "state"], web=True, writes=True, max_turns_key="light", search_cap_key="light",
        prompt_file="trade_review.md", effort="high"),
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


def get(name):
    if name not in JOBS:
        raise KeyError(f"unknown job {name}; known: {', '.join(JOBS)}")
    return JOBS[name]


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
    "read_briefs (the agent's recent briefs, trade reviews included), read_season_log, read_research, "
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


def system_prompt(spec, cfg, rules_prose):
    persona = read_prompt("persona.md")
    now = datetime.now(roster._eastern())
    body = render(persona, team_id=cfg.team_id, now=now.strftime("%A %B %d, %Y %I:%M %p ET"),
                  writes="enabled" if not cfg.dry_run else "DISABLED (dry run: previews only, nothing posts)")
    if spec.owner_job:
        body += "\n\n# Rules\n\n" + rules_prose
    return body


def user_prompt(spec, ctx, params):
    template = read_prompt(spec.prompt_file)
    now = datetime.now(roster._eastern())
    cfg = ctx.cfg
    values = {
        "week": ctx.week, "scoring_period": ctx.scoring_period,
        # ESPN rolls its current week on Tuesday morning, so "the week just
        # played" is one behind unless the caller says which.
        "played_week": int((params or {}).get("week") or max(1, ctx.week - 1)),
        "now": now.strftime("%A %B %d, %Y %I:%M %p ET"),
        "team": ctx.team_name(cfg.team_id), "team_id": cfg.team_id, "owner_team_id": cfg.team_id,
        "search_cap": cfg.heavy_search_cap if spec.search_cap_key == "heavy" else cfg.light_search_cap,
        "owner_note": "",
    }
    values.update({k: v for k, v in (params or {}).items() if isinstance(v, (str, int, float))})
    if "players" in (params or {}):
        values["players"] = ", ".join(params["players"])
    return render(template, **values)
