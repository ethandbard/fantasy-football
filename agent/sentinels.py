"""
The roster integrity sentinel: plain Python, no model, every hour.

It reads the roster and looks for the states that cost this team points
in the past: a healthy player stuck in the IR slot (ESPN then refuses every
add and trade), a starter on bye or ruled out, an empty starting slot, a
roster with no kicker or no D/ST, a third quarterback, and a lineup the
model's numbers say is several points short of the best one.

A finding with one obvious fix is fixed here (a healthy player moves from
IR to the bench when the roster has room). Anything that needs judgment
wakes a model run: the lineup worker for lineup findings, the general
manager for findings that need an add or a drop. Each finding wakes a run
at most once per WAKE_INTERVAL, so a problem nobody can fix yet (a locked
player) does not start a run every hour.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import gamedaybot.espn.roster as roster
import gamedaybot.espn.writes as writes
from agent import discord_out, store
from agent.analytics import model

logger = logging.getLogger(__name__)

FIX, LINEUP, GM = "fix", "lineup", "gm"
WAKE_INTERVAL = timedelta(hours=12)
# A lineup the model rates this many points below the best one wakes the lineup worker.
GAP_POINTS = 3.0
IR_TAGS = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
CANNOT_PLAY = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION"}


@dataclass
class Finding:
    kind: str
    text: str
    route: str
    players: List[int] = field(default_factory=list)
    moves: List[dict] = field(default_factory=list)   # lineup items for a FIX

    @property
    def fingerprint(self):
        return f"{self.kind}:{','.join(str(p) for p in sorted(self.players))}"


def _tag(e):
    return str(e.injury_status or "ACTIVE").upper()


def _can_play(e):
    return not e.bye and _tag(e) not in CANNOT_PLAY


def _bench_fill(entries, slot_id):
    """An unlocked bench player who can play and fits the slot, best projection first."""
    pool = [e for e in entries if e.slot_id == roster.BENCH_SLOT and not e.lineup_locked
            and slot_id in e.eligible_slot_ids and _can_play(e)]
    return max(pool, key=lambda e: e.projected or 0) if pool else None


def inspect(entries, slot_counts, values=None, rules=None):
    """
    Findings for one roster. Pure, so it can be tested.

    entries: RosterEntry list; slot_counts: {slot_id: count}; values:
    {player_id: this week's value} from the model (ESPN projections when
    missing); rules: the loaded rules (ceilings and tags).
    """
    rules = rules or {}
    ir_tags = {t.upper() for t in rules.get("ir_tags", IR_TAGS)}
    findings = []
    spots = roster.open_spots(entries)

    for e in entries:
        if e.slot_id == roster.IR_SLOT and _tag(e) not in ir_tags:
            if spots > 0 and not e.lineup_locked:
                findings.append(Finding("ir_illegal", f"{e.name} sits in IR tagged {_tag(e)}; moving him to the bench "
                                        f"(the roster has {spots} open spot{'s' if spots != 1 else ''})", FIX,
                                        [e.player_id], [{"player_id": e.player_id, "from_slot": roster.IR_SLOT,
                                                         "to_slot": roster.BENCH_SLOT}]))
            else:
                why = "he is locked" if e.lineup_locked else "the roster is full, so a drop is needed first"
                findings.append(Finding("ir_illegal", f"{e.name} sits in IR tagged {_tag(e)} and ESPN refuses every add "
                                        f"and trade until he leaves it; {why}", GM, [e.player_id]))

    for e in entries:
        if not e.is_starter or _can_play(e) or e.lineup_locked:
            continue
        why = "on bye" if e.bye else f"tagged {_tag(e)}"
        fill = _bench_fill(entries, e.slot_id)
        if fill is not None:
            findings.append(Finding("starter_cannot_play", f"{e.name} starts at {e.slot} but is {why}; "
                                    f"{fill.name} can fill the slot", LINEUP, [e.player_id, fill.player_id]))
        else:
            findings.append(Finding("starter_cannot_play", f"{e.name} starts at {e.slot} but is {why}, and no bench "
                                    "player can fill the slot; a free-agent add is needed", GM, [e.player_id]))

    used = {}
    for e in entries:
        if e.is_starter:
            used[e.slot_id] = used.get(e.slot_id, 0) + 1
    for slot_id, count in (slot_counts or {}).items():
        if used.get(slot_id, 0) < count:
            fill = _bench_fill(entries, slot_id)
            if fill is not None:
                findings.append(Finding("empty_slot", f"{roster.slot_name(slot_id)} has an empty slot; {fill.name} can "
                                        "fill it", LINEUP, [fill.player_id]))
            else:
                findings.append(Finding("empty_slot", f"{roster.slot_name(slot_id)} has an empty slot and no bench "
                                        "player can fill it", GM, [slot_id]))

    by_pos = {}
    for e in entries:
        if e.slot_id != roster.IR_SLOT:
            by_pos[e.position] = by_pos.get(e.position, 0) + 1
    if by_pos.get("K", 0) == 0:
        findings.append(Finding("no_k", "the roster has no kicker", GM, ["K"]))
    if by_pos.get("D/ST", 0) == 0:
        findings.append(Finding("no_dst", "the roster has no D/ST", GM, ["D/ST"]))
    if by_pos.get("QB", 0) > rules.get("max_qb", 2):
        findings.append(Finding("third_qb", f"the roster carries {by_pos['QB']} quarterbacks", GM, ["QB"]))
    if by_pos.get("K", 0) > rules.get("max_k", 1):
        findings.append(Finding("second_k", f"the roster carries {by_pos['K']} kickers", GM, ["K2"]))

    gap = lineup_gap(entries, slot_counts, values)
    if gap is not None:
        findings.append(gap)
    return findings


def lineup_gap(entries, slot_counts, values=None, threshold=GAP_POINTS):
    """A LINEUP finding when the best lineup beats the current one by `threshold` points, else None."""
    values = values or {}

    def value(e):
        v = values.get(e.player_id)
        return float(v if v is not None else (e.projected or 0.0))

    playable = [e for e in entries if e.slot_id != roster.IR_SLOT and _can_play(e)]
    counts = {roster.slot_name(s): n for s, n in (slot_counts or {}).items()}
    players = [{"name": e.name, "position": e.position, "value": value(e), "player_id": e.player_id,
                "eligible": [roster.slot_name(s) for s in e.eligible_slot_ids if s not in (roster.BENCH_SLOT, roster.IR_SLOT)]}
               for e in playable]
    best = model.best_lineup(players, counts)
    best_ids = {p["player_id"] for _, p in best}
    current = [e for e in entries if e.is_starter]
    cur_ids = {e.player_id for e in current}
    if best_ids == cur_ids:
        return None
    gain = sum(p["value"] for _, p in best) - sum(value(e) for e in current)
    if gain < threshold:
        return None
    changing = [e for e in entries if (e.player_id in best_ids) != (e.player_id in cur_ids)]
    if any(e.lineup_locked for e in changing):
        return None
    swaps_in = [e.name for e in changing if e.player_id in best_ids]
    swaps_out = [e.name for e in changing if e.player_id in cur_ids]
    return Finding("lineup_gap", f"the best lineup scores about {gain:.1f} more: start {', '.join(swaps_in)} over "
                   f"{', '.join(swaps_out)}", LINEUP, [e.player_id for e in changing])


def week_values(data_dir, week):
    """{player_id: this week's model mean} from the week's analytics build, or {} when there is none."""
    from agent.analytics import pipeline
    path = pipeline.analytics_dir(data_dir, week) / "analysis.json"
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out = {}
    for row in (body.get("tables") or {}).get("players") or (body.get("tables") or {}).get("my_roster") or []:
        if row.get("player_id") is not None and row.get("modelled") and row.get("mean") is not None:
            out[int(row["player_id"])] = float(row["mean"])
    return out


def _recently_woken(fingerprint, now):
    stamp = store.get_note(f"sentinel:{fingerprint}")
    if not stamp:
        return False
    try:
        return now - datetime.fromisoformat(stamp) < WAKE_INTERVAL
    except ValueError:
        return False


def check(ctx, cfg, queue, now=None):
    """
    One sentinel pass against the live roster. Returns {"findings", "fixed",
    "woken"} for the log and /agent status.
    """
    now = now or datetime.now(timezone.utc)
    entries = ctx.my_roster(refresh=True)
    findings = inspect(entries, ctx.slot_counts(), week_values(cfg.data_dir, ctx.week), ctx.rules)
    fixed, woken = [], []
    for f in [f for f in findings if f.route == FIX]:
        payload = writes.lineup_payload(cfg.team_id, cfg.swid, ctx.scoring_period, f.moves)
        result = ctx.writer.post(payload)
        store.log_transaction("lineup", payload, result, description=f.text, reason="integrity sentinel")
        line = f"Integrity sentinel: {f.text}: {result.summary()}"
        fixed.append(line)
        discord_out.line(cfg, line, kind="info" if result.ok else "alert")
    if fixed:
        entries = ctx.my_roster(refresh=True)
        findings = [f for f in findings if f.route != FIX] + [
            f for f in inspect(entries, ctx.slot_counts(), week_values(cfg.data_dir, ctx.week), ctx.rules)
            if f.route == FIX]
    for route, job, params in ((LINEUP, "lineup", {}), (GM, "gm", {"situation": "integrity"})):
        mine = [f for f in findings if f.route == route]
        fresh = [f for f in mine if not _recently_woken(f.fingerprint, now)]
        if not fresh:
            continue
        text = "; ".join(f.text for f in mine)
        run_id = queue.submit(job, params=dict(params, week=ctx.week, findings=text,
                                               reason="integrity sentinel"), trigger="sentinel")
        for f in fresh:
            store.set_note(f"sentinel:{f.fingerprint}", now.isoformat(timespec="minutes"))
        woken.append(f"{job} ({run_id}): {text}")
        logger.info("integrity sentinel woke %s as %s: %s", job, run_id, text)
    store.set_note("integrity", {"at": now.isoformat(timespec="minutes"), "findings": [f.text for f in findings],
                                 "fixed": fixed, "woken": woken})
    return {"findings": [f.text for f in findings], "fixed": fixed, "woken": woken}
