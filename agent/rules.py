"""
The permission rules, as data the policy enforces and as prose the model reads.

Defaults live here. `rules.json` in the agent data directory overrides any
key, so the core list or the deadline can change without a redeploy. The
prose the model sees is RULES.md, read from the data directory if a copy is
there, otherwise from the repository's fantasy-football-agents folder.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULTS = {
    # Players the agent may never drop or trade away on its own. Names are
    # matched case-insensitively on the full ESPN name.
    "core_players": ["Jahmyr Gibbs", "Bucky Irving", "Emeka Egbuka", "Rashee Rice", "Zay Flowers"],
    # ISO date of the league's trade deadline; no trade action within 24 hours of it.
    "trade_deadline": "2026-12-02T23:59:00-05:00",
    # Roster composition ceilings. More than this at a position is never allowed.
    "max_qb": 2,
    "max_k": 1,
    "max_dst": 2,
    # A run may execute at most this many adds (free-agent or waiver) by itself.
    "max_adds_per_run": 3,
    # Trade proposals the agent may have open at once.
    "max_open_proposals": 3,
    # Hours before a pending ask expires unanswered.
    "ask_expiry_hours": 24,
    # Injury tags that make a player IR-eligible. ESPN allows only Out or a
    # formal IR/suspension designation; Doubtful and Questionable are not enough.
    "ir_tags": ["OUT", "INJURY_RESERVE", "SUSPENSION"],
    # Tags (and a bye) that mean a player in a starting slot cannot play this
    # week. Dropping such a player is not "dropping a starter".
    "cannot_play_tags": ["OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION"],
    # Hour (Eastern) at which ESPN processes waiver claims, every day but Tuesday.
    "waiver_process_hour": 3,
    # Hours before an ask's deadline at which the owner gets one reminder.
    "ask_reminder_hours": 3,
}


def load(data_dir):
    rules = dict(DEFAULTS)
    path = Path(data_dir) / "rules.json"
    if path.exists():
        try:
            rules.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning("rules.json unreadable, using defaults: %s", e)
    rules["core_players_lower"] = {n.lower() for n in rules.get("core_players", [])}
    return rules


def prose(data_dir, repo_agents_dir):
    for base in (Path(data_dir), Path(repo_agents_dir)):
        path = base / "RULES.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
    return "No RULES.md found. Treat every write as ask-first."
