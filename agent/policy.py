"""
Classifies a proposed roster move into a permission tier: auto, ask, or never.

The model plans; this decides. It sees the move and the roster it would act
on and returns a Decision the write tools obey. Every rule here mirrors a
line in fantasy-football-agents/RULES.md.

Two modes, chosen by the "autonomy" rule. "full" (the default) lets the
agent execute anything it judges right: every ask and every rule-based never
becomes auto, and only a hard never stands, meaning a move ESPN itself
refuses or one that undoes the owner's own action. "tiered" applies every
tier below as written.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List

from gamedaybot.espn.roster import BENCH_SLOT, IR_SLOT

AUTO, ASK, NEVER = "auto", "ask", "never"
FULL, TIERED = "full", "tiered"


@dataclass
class Decision:
    tier: str
    reasons: List[str] = field(default_factory=list)
    # A never that full autonomy does not lift: ESPN refuses the move anyway,
    # or it would undo something the owner did himself.
    hard: bool = False

    def __str__(self):
        return f"{self.tier}: " + ("; ".join(self.reasons) if self.reasons else "no rule triggered")


def full_autonomy(rules):
    return str(rules.get("autonomy", FULL)).strip().lower() != TIERED


def settle(decision, rules):
    """Apply the autonomy mode: under full autonomy everything but a hard never runs as auto."""
    if not full_autonomy(rules) or decision.tier == AUTO or decision.hard:
        return decision
    lifted = f"would be {decision.tier} under the tiered rules ({'; '.join(decision.reasons)}); full autonomy is on"
    return Decision(AUTO, [lifted])


def _name(entry):
    return (entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", "")) or ""


def _pos(entry):
    return (entry.get("position") if isinstance(entry, dict) else getattr(entry, "position", "")) or ""


def _slot(entry):
    return entry.get("slot_id") if isinstance(entry, dict) else getattr(entry, "slot_id", BENCH_SLOT)


def _is_core(entry, rules):
    return _name(entry).lower() in rules.get("core_players_lower", set())


def _is_starter(entry):
    return _slot(entry) not in (BENCH_SLOT, IR_SLOT)


def _get(entry, key, default=None):
    return entry.get(key, default) if isinstance(entry, dict) else getattr(entry, key, default)


def _can_play(entry, rules):
    """A starter who is tagged out (or doubtful, or suspended) or on bye holds a slot but not a game."""
    if _get(entry, "bye", False):
        return False
    tag = str(_get(entry, "injury_status", "") or "").upper()
    return tag not in {t.upper() for t in rules.get("cannot_play_tags", [])}


def _undroppable(entry):
    return _get(entry, "droppable", True) is False


def _count(roster, position):
    return sum(1 for e in roster if _pos(e) == position)


def _composition_after(roster, adds, drops):
    """Position counts after the move, as {position: count}."""
    drop_ids = {e.get("player_id") if isinstance(e, dict) else e.player_id for e in drops}
    remaining = [e for e in roster if (e.get("player_id") if isinstance(e, dict) else e.player_id) not in drop_ids]
    counts = {}
    for e in remaining + list(adds):
        counts[_pos(e)] = counts.get(_pos(e), 0) + 1
    return counts


def _near_deadline(rules, now=None):
    raw = rules.get("trade_deadline")
    if not raw:
        return False
    try:
        deadline = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now >= deadline - timedelta(hours=24)


def _classify_lineup(moves, roster, rules):
    """Lineup moves are auto. The legality check happens in the write tool."""
    return Decision(AUTO, ["lineup changes are always auto"])


def _classify_add_drop(adds, drops, roster, rules):
    reasons = []
    tier = AUTO
    # ESPN's own refusal first: it is the one never that full autonomy keeps.
    for e in drops:
        if _undroppable(e):
            return Decision(NEVER, [f"{_name(e)} is on ESPN's undroppable list"], hard=True)
    for e in drops:
        if _is_core(e, rules):
            return Decision(NEVER, [f"{_name(e)} is on the core list"])
    after = _composition_after(roster, adds, drops)
    if after.get("QB", 0) > rules.get("max_qb", 2):
        return Decision(NEVER, ["that would carry a third QB"])
    if after.get("K", 0) > rules.get("max_k", 1):
        return Decision(NEVER, ["that would carry a second kicker"])
    for e in drops:
        if _is_starter(e) and _can_play(e, rules):
            tier = ASK
            reasons.append(f"{_name(e)} is a current starter")
        elif _is_starter(e):
            reasons.append(f"{_name(e)} holds a starting slot but cannot play this week")
    if _count(roster, "K") and after.get("K", 0) == 0:
        tier = ASK
        reasons.append("roster would have no kicker")
    if _count(roster, "D/ST") and after.get("D/ST", 0) == 0:
        tier = ASK
        reasons.append("roster would have no D/ST")
    if after.get("D/ST", 0) > rules.get("max_dst", 2):
        tier = ASK
        reasons.append("more than two D/STs")
    if tier == AUTO:
        reasons.append("every drop is a bench player, or a starter who cannot play this week, outside the core list")
    return Decision(tier, reasons)


def _classify_trade_propose(gives, gets, roster, rules, now=None):
    if _near_deadline(rules, now):
        return Decision(NEVER, ["within 24 hours of the trade deadline"])
    for e in gives:
        if _is_core(e, rules):
            return Decision(NEVER, [f"{_name(e)} is on the core list"])
    after = _composition_after(roster, gets, gives)
    if after.get("QB", 0) > rules.get("max_qb", 2):
        return Decision(NEVER, ["that would carry a third QB"])
    if after.get("K", 0) > rules.get("max_k", 1):
        return Decision(NEVER, ["that would carry a second kicker"])
    return Decision(ASK, ["trade proposals always need approval"])


def _classify_trade_respond(accept, gives, gets, roster, rules, now=None):
    if _near_deadline(rules, now):
        return Decision(NEVER, ["within 24 hours of the trade deadline"])
    if not accept:
        return Decision(AUTO, ["declining an offer is auto"])
    for e in gives:
        if _is_core(e, rules):
            return Decision(NEVER, [f"{_name(e)} is on the core list"])
    return Decision(ASK, ["accepting a trade always needs approval"])


def _classify_withdraw(offer_id, agent_proposed_ids, rules, now=None):
    if str(offer_id) not in {str(i) for i in agent_proposed_ids}:
        return Decision(NEVER, ["that proposal was not sent by the agent"], hard=True)
    return Decision(AUTO, ["withdrawing the agent's own proposal"])


def _classify_cancel_waiver(rules):
    return Decision(AUTO, ["cancelling a queued claim is auto"])


# ------------------------------------------------------------ public entry points


def classify_lineup(moves, roster, rules):
    return settle(_classify_lineup(moves, roster, rules), rules)


def classify_add_drop(adds, drops, roster, rules):
    return settle(_classify_add_drop(adds, drops, roster, rules), rules)


def classify_trade_propose(gives, gets, roster, rules, now=None):
    return settle(_classify_trade_propose(gives, gets, roster, rules, now), rules)


def classify_trade_respond(accept, gives, gets, roster, rules, now=None):
    return settle(_classify_trade_respond(accept, gives, gets, roster, rules, now), rules)


def classify_withdraw(offer_id, agent_proposed_ids, rules, now=None):
    return settle(_classify_withdraw(offer_id, agent_proposed_ids, rules, now), rules)


def classify_cancel_waiver(rules):
    return settle(_classify_cancel_waiver(rules), rules)
