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
    "postwaiver": (["get_recent_activity", "get_my_roster", "read_state", "append_season_log"], 1),
    # A league-wide roundup plus at least one team-level search.
    "designations": (["get_my_roster", "append_season_log"], 2),
    "pregame": (["get_my_roster", "read_state"], 1),
    "lineup": (["get_my_roster", "read_state"], 0),
    "wire": (["get_my_roster", "read_strategy", "get_player"], 0),
}

# The general manager's required calls depend on the situation. Every
# situation starts from its memory and ends in the season log; the weekly
# session also scouts and rewrites the strategy, and an offer review must
# evaluate before it answers.
GM_COMMON = ["read_strategy", "read_negotiations", "get_my_roster", "append_season_log"]
REQUIRED_GM = {
    "weekly": (GM_COMMON + ["read_analytics", "read_scorecard", "get_week_results", "get_team_roster",
                            "get_free_agents", "market_map", "playoff_odds", "write_research", "write_state",
                            "schedule_pregame_checks", "write_strategy"], 4),
    "trade_desk": (GM_COMMON + ["get_agent_activity", "market_map"], 1),
    "after_games": (GM_COMMON + ["get_week_results", "market_map"], 1),
    "offer": (GM_COMMON + ["get_team_roster", "evaluate_trade"], 1),
    "integrity": (["get_my_roster", "append_season_log"], 0),
    "manual": (GM_COMMON, 0),
}

_SKIPPED_HEADING = re.compile(r"steps skipped", re.IGNORECASE)


def short_name(tool_name):
    """mcp__espn__get_my_roster -> get_my_roster; built-ins pass through."""
    return tool_name.rsplit("__", 1)[-1]


def check(job, calls, brief, owner_job=True, situation=None):
    """The problems with a finished run, as short lines; [] when it did every required step.

    calls counts tool calls by short name, including denied ones.
    """
    calls = Counter(calls)
    problems = []
    if job == "gm":
        required, min_web = REQUIRED_GM.get(situation or "manual", REQUIRED_GM["manual"])
    else:
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
