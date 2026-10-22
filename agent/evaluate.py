"""
Trade evaluation and playoff odds from the model's numbers, no model in the loop.

The general manager's question about any roster change is "does this raise
the chance of making the playoffs, and would the other manager say yes".
This module answers both with arithmetic:

- A season simulation. Every team's weekly score is the sum of its best
  lineup's per-week forecasts (the analytics build's by_week means, ESPN's
  numbers for players the model does not cover), drawn with the position's
  spread plus a floor of noise for what forecasts cannot see. Remaining
  regular-season games are played out a few thousand times and the
  playoff spots are counted, before and after a change.
- Lineup deltas for both teams, per remaining week, in two views: the
  model's, which is our edge, and ESPN's season averages, which is how most
  managers judge an offer in the app.
- Chart totals for both sides from the week's trade value chart.
- Roster fit: who starts, who sits, and the drops a trade would force.

Everything here is pure over a State built once per run by build_state.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

import gamedaybot.espn.roster as roster
from agent.analytics import model

ROSTER_LIMIT = roster.ROSTER_LIMIT
LONG_OUT = {"INJURY_RESERVE", "SUSPENSION"}
# nflverse team codes that differ from ESPN's.
NFLVERSE_TO_ESPN = {"LA": "LAR", "WAS": "WSH"}
# Draws add this much standard deviation, scaled by the league's weekly spread,
# for what the forecasts cannot see: injuries, lineup mistakes, game script.
NOISE_SHARE = 0.45
SIMS = 3000


@dataclass
class Player:
    player_id: int
    name: str
    position: str
    pro_team: str
    eligible: List[str]
    team_id: Optional[int]
    injury: str = "ACTIVE"
    bye_now: bool = False
    espn_proj: Optional[float] = None
    espn_avg: Optional[float] = None
    modelled: bool = False
    mean: Optional[float] = None
    by_week: Dict[int, float] = field(default_factory=dict)
    ros_pg: Optional[float] = None
    locked: bool = False
    slot: str = "BE"

    @property
    def starter_slots(self):
        return [s for s in self.eligible if s not in ("BE", "IR")]


@dataclass
class Team:
    team_id: int
    name: str
    wins: float
    losses: float
    points_for: float
    roster: List[int]
    ir: List[int] = field(default_factory=list)


@dataclass
class State:
    week: int
    reg_weeks: int
    playoff_teams: int
    slot_counts: Dict[str, int]
    teams: Dict[int, Team]
    players: Dict[int, Player]
    games: List[tuple]                 # (week, home_id, away_id) still to play
    history: Dict[int, List[float]]    # played weekly scores per team
    me: int
    cvs: Dict[str, float] = field(default_factory=dict)
    team_byes: Dict[str, List[int]] = field(default_factory=dict)
    chart: object = None               # agent.chart.Chart or None
    data_through: Optional[int] = None

    @property
    def weeks(self):
        return list(range(self.week, self.reg_weeks + 1))


# ------------------------------------------------------------------ values


def _injury_factor(player, week, now_week):
    tag = str(player.injury or "ACTIVE").upper()
    ahead = week - now_week
    if tag in LONG_OUT:
        return 0.0 if ahead <= 2 else 1.0
    if tag == "OUT":
        return 0.0 if ahead == 0 else (0.5 if ahead == 1 else 1.0)
    if tag == "DOUBTFUL":
        return 0.25 if ahead == 0 else 1.0
    return 1.0


def on_bye(state, player, week):
    if week == state.week and player.bye_now:
        return True
    return week in state.team_byes.get(player.pro_team, [])


def value(state, player, week, view="model"):
    """One player's expected points in a week: the model's forecast, or ESPN's season average (view='espn')."""
    if on_bye(state, player, week):
        return 0.0
    if view == "model" and player.by_week and week in player.by_week:
        base = float(player.by_week[week])
    elif view == "model" and player.modelled and player.mean is not None and week == state.week:
        base = float(player.mean)
    else:
        if week == state.week and player.espn_proj is not None:
            base = float(player.espn_proj)
        else:
            base = float(player.espn_avg if player.espn_avg is not None else (player.espn_proj or 0.0))
        if view == "espn" and week != state.week and player.espn_avg is None and player.espn_proj is not None:
            base = float(player.espn_proj)
    return max(base, 0.0) * _injury_factor(player, week, state.week)


def ros_value(state, player, view="model"):
    weeks = state.weeks or [state.week]
    return sum(value(state, player, w, view) for w in weeks) / max(len(weeks), 1)


def best_lineup(state, roster_ids, week, view="model"):
    """[(slot, Player, value)] for the best legal lineup from these players in a week."""
    pool = []
    for pid in roster_ids:
        p = state.players.get(pid)
        if p is None or p.slot == "IR":
            continue
        v = value(state, p, week, view)
        pool.append({"name": pid, "position": p.position, "value": v, "eligible": p.starter_slots, "player": p})
    picked = model.best_lineup(pool, state.slot_counts)
    return [(slot, d["player"], d["value"]) for slot, d in picked]


def team_week(state, roster_ids, week, view="model"):
    """(mean, sd, lineup) for a team's best lineup in a week."""
    lineup = best_lineup(state, roster_ids, week, view)
    mean = sum(v for _, _, v in lineup)
    var = sum(model.sd_for(v, p.position, state.cvs) ** 2 for _, p, v in lineup)
    return mean, math.sqrt(var), lineup


# --------------------------------------------------------------- changes


def apply_changes(state, changes, me=None):
    """
    {team_id: [player_ids]} after a list of changes, plus the drops each
    team would be forced into to fit the roster limit, as
    {team_id: [player_id]}. Change shapes:
      {"type": "trade", "other_team_id": X, "give": [...], "get": [...]}
      {"type": "add", "player_id": P, "drop": [...]}   (my team)
      {"type": "drop", "drop": [...]}                 (my team)
    """
    me = me if me is not None else state.me
    rosters = {tid: list(t.roster) for tid, t in state.teams.items()}
    forced = {}
    for c in changes or []:
        kind = c.get("type", "trade")
        if kind == "trade":
            other = int(c["other_team_id"])
            give = [int(x) for x in c.get("give") or []]
            get = [int(x) for x in c.get("get") or []]
            rosters[me] = [p for p in rosters[me] if p not in give] + get
            rosters[other] = [p for p in rosters[other] if p not in get] + give
        elif kind == "add":
            drops = [int(x) for x in c.get("drop") or []]
            rosters[me] = [p for p in rosters[me] if p not in drops] + [int(c["player_id"])]
        elif kind == "drop":
            drops = [int(x) for x in c.get("drop") or []]
            rosters[me] = [p for p in rosters[me] if p not in drops]
    for tid, ids in rosters.items():
        active = [p for p in ids if p not in state.players or state.players[p].slot != "IR"]
        extra = len(active) - ROSTER_LIMIT
        if extra > 0:
            worst = sorted(active, key=lambda pid: ros_value(state, state.players[pid]) if pid in state.players else 0)
            cut = worst[:extra]
            forced[tid] = cut
            rosters[tid] = [p for p in ids if p not in cut]
    return rosters, forced


# ------------------------------------------------------------- simulation


def simulate(state, rosters=None, sims=SIMS, seed=0):
    """
    {team_id: {"playoff_odds", "avg_wins", "top_seed_odds", "mean_week"}} over the
    remaining regular season. Deterministic under seed, so before and after a
    change are comparable.
    """
    rosters = rosters or {tid: t.roster for tid, t in state.teams.items()}
    ids = sorted(state.teams)
    index = {tid: i for i, tid in enumerate(ids)}
    n = len(ids)
    weeks = state.weeks
    means = np.zeros((n, max(len(weeks), 1)))
    sds = np.zeros_like(means)
    for tid in ids:
        for j, w in enumerate(weeks):
            m, s, _ = team_week(state, rosters[tid], w)
            means[index[tid], j] = m
            sds[index[tid], j] = s
    # Level calibration: the forecasts sum to what the model expects of a
    # lineup, the history says what lineups in this league actually score.
    played = [s for scores in state.history.values() for s in scores if s]
    scale, noise = 1.0, 0.0
    if played and means.any():
        scale = float(np.clip(np.mean(played) / max(means.mean(), 1e-6), 0.8, 1.25))
        noise = NOISE_SHARE * float(np.std(played)) if len(played) > 3 else 0.0
    means = means * scale
    sds = np.sqrt(sds ** 2 + noise ** 2)
    wins_now = np.array([state.teams[t].wins for t in ids], float)
    pf_now = np.array([state.teams[t].points_for for t in ids], float)
    games = [(w, h, a) for (w, h, a) in state.games if w in weeks]
    rng = np.random.default_rng(seed)
    wins = np.tile(wins_now, (sims, 1))
    pf = np.tile(pf_now, (sims, 1))
    if games:
        wi = np.array([weeks.index(w) for w, _, _ in games])
        hi = np.array([index[h] for _, h, _ in games])
        ai = np.array([index[a] for _, _, a in games])
        hp = rng.normal(means[hi, wi], sds[hi, wi], size=(sims, len(games)))
        ap = rng.normal(means[ai, wi], sds[ai, wi], size=(sims, len(games)))
        hw = hp > ap
        tie = hp == ap
        for g in range(len(games)):
            wins[:, hi[g]] += hw[:, g] + 0.5 * tie[:, g]
            wins[:, ai[g]] += (~hw[:, g] & ~tie[:, g]) + 0.5 * tie[:, g]
            pf[:, hi[g]] += hp[:, g]
            pf[:, ai[g]] += ap[:, g]
    key = wins * 1e6 + pf
    order = np.argsort(-key, axis=1, kind="stable")
    rank = np.empty_like(order)
    rank[np.arange(sims)[:, None], order] = np.arange(n)
    out = {}
    for tid in ids:
        i = index[tid]
        out[tid] = {"playoff_odds": round(float((rank[:, i] < state.playoff_teams).mean()), 3),
                    "top_seed_odds": round(float((rank[:, i] == 0).mean()), 3),
                    "avg_wins": round(float(wins[:, i].mean()), 2),
                    "mean_week": round(float(means[i].mean()), 1)}
    return out


def playoff_odds(state, changes=None, sims=SIMS):
    """Odds for every team before and after the changes. Returns (before, after, forced_drops)."""
    before = simulate(state, sims=sims)
    if not changes:
        return before, before, {}
    rosters, forced = apply_changes(state, changes)
    after = simulate(state, rosters, sims=sims)
    return before, after, forced


# -------------------------------------------------------------- evaluation


def lineup_deltas(state, team_id, before_ids, after_ids, view="model"):
    """Per-week best-lineup totals before and after for one team."""
    rows = []
    for w in state.weeks:
        b, _, _ = team_week(state, before_ids, w, view)
        a, _, _ = team_week(state, after_ids, w, view)
        rows.append({"week": w, "before": round(b, 1), "after": round(a, 1), "delta": round(a - b, 1)})
    total = sum(r["delta"] for r in rows)
    return {"weeks": rows, "total": round(total, 1), "per_week": round(total / max(len(rows), 1), 2)}


def chart_side(state, ids):
    rows, total, missing = [], 0.0, []
    for pid in ids:
        p = state.players.get(pid)
        v = state.chart.value(p.name, p.position) if (state.chart and p) else None
        if v is None:
            missing.append(p.name if p else str(pid))
        else:
            total += v
        rows.append({"name": p.name if p else str(pid), "chart": v})
    return {"players": rows, "total": round(total, 1), "missing": missing}


def _starts(state, roster_ids, week, pid):
    return any(p.player_id == pid for _, p, _ in best_lineup(state, roster_ids, week))


def evaluate_trade(state, other_id, give, get, sims=SIMS, ledger=None):
    """Everything the general manager needs to judge one trade, as a dict; see render_trade for the text."""
    me = state.me
    other_id = int(other_id)
    give = [int(x) for x in give]
    get = [int(x) for x in get]
    problems = []
    for pid in give:
        if pid not in state.teams[me].roster:
            problems.append(f"{pid} is not on my roster")
    for pid in get:
        if pid not in state.teams.get(other_id, Team(0, "", 0, 0, 0, [])).roster:
            problems.append(f"{pid} is not on team {other_id}")
    if problems:
        return {"error": "; ".join(problems)}
    change = [{"type": "trade", "other_team_id": other_id, "give": give, "get": get}]
    before, after, forced = playoff_odds(state, change, sims=sims)
    rosters, _ = apply_changes(state, change)
    mine_before, mine_after = state.teams[me].roster, rosters[me]
    theirs_before, theirs_after = state.teams[other_id].roster, rosters[other_id]
    out = {
        "me": me, "other": other_id, "other_name": state.teams[other_id].name,
        "give": [state.players[p].name for p in give], "get": [state.players[p].name for p in get],
        "odds": {"mine_before": before[me]["playoff_odds"], "mine_after": after[me]["playoff_odds"],
                 "mine_delta": round(after[me]["playoff_odds"] - before[me]["playoff_odds"], 3),
                 "theirs_before": before[other_id]["playoff_odds"], "theirs_after": after[other_id]["playoff_odds"],
                 "theirs_delta": round(after[other_id]["playoff_odds"] - before[other_id]["playoff_odds"], 3)},
        "mine": {"model": lineup_deltas(state, me, mine_before, mine_after, "model"),
                 "espn": lineup_deltas(state, me, mine_before, mine_after, "espn")},
        "theirs": {"model": lineup_deltas(state, other_id, theirs_before, theirs_after, "model"),
                   "espn": lineup_deltas(state, other_id, theirs_before, theirs_after, "espn")},
        "chart": {"week": state.chart.week if state.chart else None,
                  "i_give": chart_side(state, give), "i_get": chart_side(state, get)},
        "forced_drops": {tid: [state.players[p].name for p in ids] for tid, ids in forced.items()},
        "fit": {
            "they_start": [state.players[p].name for p in give if _starts(state, theirs_after, state.week, p)],
            "they_bench": [state.players[p].name for p in give if not _starts(state, theirs_after, state.week, p)],
            "i_start": [state.players[p].name for p in get if _starts(state, mine_after, state.week, p)],
            "i_bench": [state.players[p].name for p in get if not _starts(state, mine_after, state.week, p)],
        },
        "players": [_player_line(state, p) for p in give + get],
        "ledger": ledger,
    }
    out["verdict"] = verdict(out)
    return out


def _player_line(state, pid):
    p = state.players[pid]
    return {"name": p.name, "position": p.position, "team": p.pro_team, "injury": p.injury,
            "ros_pg_model": round(ros_value(state, p), 1), "espn_avg": p.espn_avg,
            "byes": state.team_byes.get(p.pro_team, []),
            "chart": state.chart.value(p.name, p.position) if state.chart else None}


def verdict(ev):
    """One line from the numbers. The general manager decides; this says what the arithmetic says."""
    mine = ev["odds"]["mine_delta"]
    theirs_espn = ev["theirs"]["espn"]["per_week"]
    theirs_model = ev["theirs"]["model"]["per_week"]
    chart_in, chart_out = ev["chart"]["i_get"]["total"], ev["chart"]["i_give"]["total"]
    if mine <= 0.0:
        return (f"the arithmetic says no: my playoff odds move {mine:+.1%}; the trade does not help this season")
    if theirs_espn >= -0.5:
        how = "gains" if theirs_espn > 0.5 else "breaks even"
        return (f"send it: my playoff odds move {mine:+.1%} and his lineup {how} on ESPN's numbers "
                f"({theirs_espn:+.1f}/wk ESPN, {theirs_model:+.1f}/wk model); chart {chart_out:.0f} out for {chart_in:.0f} in")
    return (f"good for me ({mine:+.1%} playoff odds) but his lineup loses {theirs_espn:+.1f}/wk in his own view; "
            "expect a decline unless the offer fills a hole he feels or the chart flatters him "
            f"(chart {chart_out:.0f} out for {chart_in:.0f} in)")


# ---------------------------------------------------------------- market


def typical_starters(state, view="model"):
    """{slot: median value of the slot's starter across the league} on rest-of-season rates."""
    per_slot = {}
    for tid, t in state.teams.items():
        lineup = _ros_lineup(state, t.roster, view)
        for slot, p, v in lineup:
            per_slot.setdefault(slot, []).append(v)
    return {s: float(np.median(v)) for s, v in per_slot.items() if v}


def _ros_lineup(state, roster_ids, view="model"):
    pool = []
    for pid in roster_ids:
        p = state.players.get(pid)
        if p is None:
            continue
        pool.append({"name": pid, "position": p.position, "value": ros_value(state, p, view),
                     "eligible": p.starter_slots, "player": p})
    return [(s, d["player"], d["value"]) for s, d in model.best_lineup(pool, state.slot_counts)]


def market_map(state, odds=None, negotiations=None):
    """One dict per team: standing, odds, weak slots, surplus, byes ahead, roster room, and the thread with us."""
    odds = odds or simulate(state)
    typical = typical_starters(state)
    negotiations = negotiations or {}
    out = []
    for tid, t in sorted(state.teams.items(), key=lambda kv: -odds[kv[0]]["playoff_odds"]):
        lineup = _ros_lineup(state, t.roster)
        starters = {p.player_id for _, p, _ in lineup}
        slots = []
        for slot, p, v in lineup:
            gap = v - typical.get(slot, v)
            slots.append({"slot": slot, "name": p.name, "ros_pg": round(v, 1), "gap": round(gap, 1)})
        filled = {s for s, _, _ in lineup}
        for slot, n in state.slot_counts.items():
            have = sum(1 for s, _, _ in lineup if s == slot)
            for _ in range(n - have):
                slots.append({"slot": slot, "name": "(empty)", "ros_pg": 0.0, "gap": round(-typical.get(slot, 0), 1)})
        weak = sorted(slots, key=lambda s: s["gap"])[:3]
        surplus = []
        for pid in t.roster:
            p = state.players.get(pid)
            if p is None or pid in starters or pid in t.ir:
                continue
            v = ros_value(state, p)
            best_slot = max((typical.get(s, 0) for s in p.starter_slots), default=0)
            if best_slot and v >= best_slot - 1.0:
                surplus.append({"name": p.name, "position": p.position, "ros_pg": round(v, 1)})
        byes = {}
        for _, p, _ in lineup:
            for w in state.team_byes.get(p.pro_team, []):
                if w in state.weeks:
                    byes.setdefault(w, []).append(p.name)
        active = [p for p in t.roster if p not in t.ir]
        thread = negotiations.get(tid)
        out.append({
            "team_id": tid, "name": t.name, "record": f"{t.wins:g}-{t.losses:g}", "points_for": round(t.points_for, 1),
            "playoff_odds": odds[tid]["playoff_odds"], "mean_week": odds[tid]["mean_week"],
            "weak_slots": weak, "surplus": sorted(surplus, key=lambda s: -s["ros_pg"])[:5],
            "byes_ahead": {w: names for w, names in sorted(byes.items())},
            "open_spots": ROSTER_LIMIT - len(active),
            "thread": thread,
        })
    return out


# ---------------------------------------------------------------- render


def _pct(x):
    return f"{x:.0%}"


def render_odds(state, before, after=None, forced=None, changes=None):
    lines = [f"Playoff odds over weeks {state.week}-{state.reg_weeks} ({state.playoff_teams} playoff spots; "
             f"forecasts through NFL week {state.data_through}):"]
    for tid in sorted(before, key=lambda t: -before[t]["playoff_odds"]):
        t = state.teams[tid]
        b = before[tid]
        line = f"{'*' if tid == state.me else ' '} {t.name:<28} {t.wins:g}-{t.losses:g}  odds {_pct(b['playoff_odds']):>4}  avg wins {b['avg_wins']:.1f}  wk mean {b['mean_week']:.1f}"
        if after is not None and after is not before:
            a = after[tid]
            line += f"  -> {_pct(a['playoff_odds']):>4} ({a['playoff_odds'] - b['playoff_odds']:+.1%})"
        lines.append(line)
    if forced:
        for tid, names in forced.items():
            lines.append(f"forced drops for {state.teams[tid].name}: {', '.join(names)}")
    return "\n".join(lines)


def render_trade(ev):
    if "error" in ev:
        return f"cannot evaluate: {ev['error']}"
    o = ev["odds"]
    lines = [
        f"Trade with {ev['other_name']} (team {ev['other']}): I give {', '.join(ev['give'])}; I get {', '.join(ev['get'])}.",
        "",
        f"VERDICT: {ev['verdict']}",
        "",
        f"Playoff odds: mine {_pct(o['mine_before'])} -> {_pct(o['mine_after'])} ({o['mine_delta']:+.1%}); "
        f"theirs {_pct(o['theirs_before'])} -> {_pct(o['theirs_after'])} ({o['theirs_delta']:+.1%}).",
        f"My lineup, model view: {ev['mine']['model']['per_week']:+.1f}/wk ({ev['mine']['model']['total']:+.1f} over the stretch); "
        f"ESPN view: {ev['mine']['espn']['per_week']:+.1f}/wk.",
        f"His lineup, model view: {ev['theirs']['model']['per_week']:+.1f}/wk; ESPN view (how he sees it): "
        f"{ev['theirs']['espn']['per_week']:+.1f}/wk.",
    ]
    ch = ev["chart"]
    if ch["week"]:
        lines.append(f"Chart (week {ch['week']}): I give {ch['i_give']['total']:.1f}, I get {ch['i_get']['total']:.1f}"
                     + (f"; not on chart: {', '.join(ch['i_give']['missing'] + ch['i_get']['missing'])}"
                        if ch['i_give']['missing'] or ch['i_get']['missing'] else ""))
    else:
        lines.append("Chart: none cached this week.")
    fit = ev["fit"]
    lines.append(f"Fit: he starts {', '.join(fit['they_start']) or 'nobody'} and benches {', '.join(fit['they_bench']) or 'nobody'}; "
                 f"I start {', '.join(fit['i_start']) or 'nobody'} and bench {', '.join(fit['i_bench']) or 'nobody'}.")
    if ev["forced_drops"]:
        for tid, names in ev["forced_drops"].items():
            lines.append(f"Forced drops for team {tid}: {', '.join(names)}.")
    lines.append("")
    lines.append("Per week (my lineup, model):  " + "  ".join(f"w{r['week']} {r['delta']:+.1f}" for r in ev["mine"]["model"]["weeks"]))
    lines.append("Per week (his lineup, ESPN):  " + "  ".join(f"w{r['week']} {r['delta']:+.1f}" for r in ev["theirs"]["espn"]["weeks"]))
    lines.append("")
    lines.append("Players: " + "; ".join(
        f"{p['name']} ({p['position']} {p['team']}{', ' + p['injury'] if p['injury'] != 'ACTIVE' else ''}) model {p['ros_pg_model']}/g, "
        f"ESPN avg {p['espn_avg']}, chart {p['chart']}, byes {p['byes'] or 'none left'}" for p in ev["players"]))
    if ev.get("ledger"):
        lines.append("")
        lines.append("Thread with this manager: " + ev["ledger"])
    return "\n".join(lines)


def render_market(rows):
    lines = []
    for r in rows:
        lines.append(f"{r['name']} (team {r['team_id']}) {r['record']}, PF {r['points_for']}, playoff odds {_pct(r['playoff_odds'])}, "
                     f"lineup {r['mean_week']}/wk, {r['open_spots']} open spot(s)")
        lines.append("  weak: " + "; ".join(f"{s['slot']} {s['name']} {s['ros_pg']} ({s['gap']:+.1f} vs typical)" for s in r["weak_slots"]))
        lines.append("  surplus: " + ("; ".join(f"{s['name']} {s['position']} {s['ros_pg']}" for s in r["surplus"]) or "none"))
        if r["byes_ahead"]:
            lines.append("  byes: " + "; ".join(f"w{w} {', '.join(n)}" for w, n in r["byes_ahead"].items()))
        if r.get("thread"):
            lines.append("  thread: " + r["thread"])
    return "\n".join(lines)
