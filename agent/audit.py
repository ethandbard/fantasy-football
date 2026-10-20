"""
A deterministic check, after each run, that the agent did the steps its
prompt makes mandatory. The prompts already ask for these steps, and the
agent has skipped them before when a step looked unnecessary (an ACTIVE tag
read as "no news"). The runner compares the run's tool calls with the table
below and posts a warning line to Discord for anything missing.

The table holds only calls every run of the job must make. Steps that depend
on what the agent finds (an add, a contingency) are left to the brief's
"Steps skipped" section.
"""
import re
from collections import Counter

WEB_TOOLS = ("WebSearch", "WebFetch")

# job -> (tools that must be called at least once, minimum web calls)
REQUIRED = {
    "research": (["read_analytics", "get_week_results", "get_my_roster", "get_free_agents",
                  "get_team_roster", "write_research", "write_state", "append_season_log"], 4),
    "plan": (["read_state", "read_research", "read_strategy", "get_agent_activity", "schedule_pregame_checks",
              "append_season_log"], 0),
    "postwaiver": (["get_recent_activity", "get_my_roster", "read_state", "append_season_log"], 1),
    # A league-wide roundup plus at least one team-level search.
    "designations": (["get_my_roster", "append_season_log"], 2),
    "pregame": (["get_my_roster", "read_state"], 1),
    "lineup": (["get_my_roster", "read_state"], 0),
    # Incoming-player research plus the bye schedule.
    "trade_review": (["get_my_roster", "get_team_roster", "read_analytics"], 2),
    "wire": (["get_my_roster", "read_strategy", "get_player"], 0),
    # The general manager scouts every roster, prices targets, and keeps the
    # strategy file. Sending an offer depends on what it finds, so it is not here.
    "gm": (["read_strategy", "get_standings", "get_agent_activity", "get_team_roster", "get_free_agents",
            "write_strategy", "append_season_log"], 3),
}

_SKIPPED_HEADING = re.compile(r"steps skipped", re.IGNORECASE)


def short_name(tool_name):
    """mcp__espn__get_my_roster -> get_my_roster; built-ins pass through."""
    return tool_name.rsplit("__", 1)[-1]


def check(job, calls, brief, owner_job=True):
    """The problems with a finished run, as short lines; [] when it did every required step.

    calls counts tool calls by short name, including denied ones.
    """
    calls = Counter(calls)
    problems = []
    required, min_web = REQUIRED.get(job, ([], 0))
    for name in required:
        if not calls.get(name):
            problems.append(f"never called {name}")
    web = sum(calls.get(t, 0) for t in WEB_TOOLS)
    if web < min_web:
        problems.append(f"{web} web searches, needs at least {min_web}")
    if owner_job and not _SKIPPED_HEADING.search(brief or ""):
        problems.append('brief has no "Steps skipped" section')
    return problems


def warning(title, run_id, problems):
    return f"⚠️ **{title}** run `{run_id}` skipped required steps: " + "; ".join(problems) + "."
