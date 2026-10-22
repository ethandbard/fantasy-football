"""
The weekly scorecard: what the agents' decisions turned into, computed
with no judgment in it, so the general manager can read its own record.

Four sections, each from data the service already holds:

- Bench points: for every played week, what the lineup scored against the
  best lineup the roster could have started, from lineup_scores.
- Claims: every waiver claim and free-agent add the agents sent, and
  whether the player arrived.
- Trades: every proposal the agents sent and every offer they declined,
  with what the players on each side scored afterward.
- Forecasts: the model's this-week means against ESPN's projections for
  the players it covered, scored on what they went on to do.

Written to data/agent/gm/scorecard.md on Tuesday mornings and read by the
general manager's weekly session through read_scorecard.
"""
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import gamedaybot.storage.db as db
from agent import store
from agent.analytics import model

logger = logging.getLogger(__name__)

FILE = Path("gm") / "scorecard.md"
BENCH = ("BE", "Bench", "IR")


# ------------------------------------------------------------- bench points


def _slot_counts_from_rows(rows):
    counts = defaultdict(int)
    for r in rows:
        if r["slot"] not in BENCH:
            counts[r["slot"]] += 1
    return dict(counts)


def bench_points(rows, slot_counts=None):
    """
    [{week, started, best, left}] for one team's weeks from lineup_scores
    rows. `left` is what the best lineup would have added.
    """
    by_week = defaultdict(list)
    for r in rows:
        by_week[int(r["week"])].append(r)
    out = []
    for week in sorted(by_week):
        week_rows = by_week[week]
        counts = slot_counts or _slot_counts_from_rows(week_rows)
        started = sum(float(r.get("points") or 0) for r in week_rows if r["slot"] not in BENCH)
        pool = [{"name": r["player_name"], "position": r.get("position"), "value": float(r.get("points") or 0),
                 "eligible": [s for s in (r.get("eligible_slots") or []) if s not in BENCH]} for r in week_rows]
        best = sum(v["value"] for _, v in model.best_lineup(pool, counts))
        out.append({"week": week, "started": round(started, 1), "best": round(best, 1), "left": round(best - started, 1)})
    return out


# ------------------------------------------------------------------ trades


def _items(payload):
    payload = json.loads(payload) if isinstance(payload, str) else (payload or {})
    return payload.get("items", [])


def points_after(points_by_player, player_id, after_week):
    return round(sum(p for w, p in points_by_player.get(int(player_id), {}).items() if w > after_week), 1)


def trade_outcomes(transactions, points_by_player, week_of, names, me):
    """
    One line per proposal sent and offer declined: what each side's players
    scored in the weeks after the decision. `week_of(at)` maps a timestamp to
    the NFL week it fell in; `names` maps player id to name.
    """
    out = []
    for t in transactions:
        if t["kind"] not in ("trade_propose", "trade_decline", "trade_accept") or not t.get("ok") or t.get("dry_run"):
            continue
        week = week_of(t["at"])
        gives = [i["playerId"] for i in _items(t["payload"]) if i.get("type") == "TRADE" and i.get("fromTeamId") == me]
        gets = [i["playerId"] for i in _items(t["payload"]) if i.get("type") == "TRADE" and i.get("toTeamId") == me]
        if t["kind"] == "trade_decline" and not gives and not gets:
            # A decline payload carries no items; the description names the players.
            out.append({"at": t["at"][:10], "kind": t["kind"], "outcome": t.get("outcome"),
                        "description": t.get("description"), "gave": None, "got": None, "week": week})
            continue
        gave = sum(points_after(points_by_player, p, week) for p in gives)
        got = sum(points_after(points_by_player, p, week) for p in gets)
        out.append({"at": t["at"][:10], "kind": t["kind"], "outcome": t.get("outcome") or "",
                    "description": t.get("description"), "week": week,
                    "gave": round(gave, 1), "got": round(got, 1),
                    "give_names": [names.get(p, str(p)) for p in gives], "get_names": [names.get(p, str(p)) for p in gets]})
    return out


# --------------------------------------------------------------- forecasts


def forecast_errors(analysis_tables_by_week, points_by_player):
    """{week: {"n", "model_mae", "espn_mae", "model_closer"}} for my roster's modelled players."""
    out = {}
    for week, table in analysis_tables_by_week.items():
        n = m_err = e_err = closer = 0
        for row in table:
            pid = row.get("player_id")
            if pid is None or not row.get("modelled") or row.get("espn_proj") in (None, 0):
                continue
            actual = points_by_player.get(int(pid), {}).get(week)
            if actual is None:
                continue
            me_ = abs(float(row["mean"]) - actual)
            ee = abs(float(row["espn_proj"]) - actual)
            n += 1
            m_err += me_
            e_err += ee
            closer += 1 if me_ < ee else 0
        if n:
            out[week] = {"n": n, "model_mae": round(m_err / n, 2), "espn_mae": round(e_err / n, 2), "model_closer": closer}
    return out


# ------------------------------------------------------------------ render


def render(week, bench, claims, trades, errors, team_name):
    lines = [f"# Scorecard through week {week}", "",
             f"_Computed {datetime.now(timezone.utc).isoformat(timespec='minutes')} with no judgment in it._", ""]
    lines.append("## Bench points left")
    if bench:
        total = sum(b["left"] for b in bench)
        lines.append("| Week | Started | Best lineup | Left on bench |")
        lines.append("|--:|--:|--:|--:|")
        for b in bench:
            lines.append(f"| {b['week']} | {b['started']} | {b['best']} | {b['left']} |")
        lines.append(f"\nTotal left on the bench: {total:.1f} points over {len(bench)} weeks "
                     f"({total / len(bench):.1f} a week).")
    else:
        lines.append("No played weeks on file.")
    lines += ["", "## Claims and adds (last 14 days)"]
    if claims:
        for c in claims:
            lines.append(f"- {c['at']} {c['description']}: {'landed' if c['landed'] else 'did not land'}")
    else:
        lines.append("None.")
    lines += ["", "## Trades: what the players did afterward"]
    if trades:
        for t in trades:
            if t.get("gave") is None:
                lines.append(f"- {t['at']} {t['kind']} ({t['outcome'] or 'done'}): {t['description']}")
                continue
            verb = {"trade_propose": "proposed", "trade_decline": "declined", "trade_accept": "accepted"}[t["kind"]]
            lines.append(f"- {t['at']} {verb}{' (' + t['outcome'] + ')' if t['outcome'] else ''}: give "
                         f"{', '.join(t['give_names']) or '-'} ({t['gave']} pts since), get "
                         f"{', '.join(t['get_names']) or '-'} ({t['got']} pts since)")
    else:
        lines.append("None.")
    lines += ["", "## Forecasts: model vs ESPN on my roster"]
    if errors:
        lines.append("| Week | Players | Model MAE | ESPN MAE | Model closer |")
        lines.append("|--:|--:|--:|--:|--:|")
        for w in sorted(errors):
            e = errors[w]
            lines.append(f"| {w} | {e['n']} | {e['model_mae']} | {e['espn_mae']} | {e['model_closer']} of {e['n']} |")
    else:
        lines.append("No weeks with both a build and a result yet.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- build


def _week_of_factory(lineup_rows, year):
    """Map a timestamp to the last NFL week that had box scores before it."""
    stamps = {}
    for r in lineup_rows:
        if r.get("year") == year and r.get("collected_at"):
            w = int(r["week"])
            stamps[w] = min(stamps.get(w, r["collected_at"]), r["collected_at"])

    def week_of(at):
        weeks = [w for w, s in stamps.items() if s <= at]
        return max(weeks) if weeks else 0
    return week_of


def build(ctx, cfg):
    """The scorecard markdown from the dashboard database and the agent's own tables."""
    from agent.analytics import pipeline
    year = cfg.year
    me = cfg.team_id
    rows = [r for r in db.get_all_lineup_scores() if r.get("year") == year]
    mine = [r for r in rows if r.get("team_id") == me]
    points_by_player = defaultdict(dict)
    names = {}
    for r in rows:
        if r.get("points") is not None:
            points_by_player[int(r["player_id"])][int(r["week"])] = float(r["points"])
            names[int(r["player_id"])] = r.get("player_name")
    played = max((int(r["week"]) for r in mine), default=0)
    bench = bench_points(mine)
    tx = store.recent_transactions(limit=300)
    week_of = _week_of_factory(rows, year)
    trades = trade_outcomes(tx, points_by_player, week_of, names, me)
    on_roster = {e.player_id for e in ctx.my_roster()}
    cutoff = (datetime.now(timezone.utc).replace(microsecond=0).timestamp() - 14 * 86400)
    claims = []
    for t in tx:
        if t["kind"] not in ("waiver", "add_drop") or not t.get("ok") or t.get("dry_run"):
            continue
        try:
            if datetime.fromisoformat(t["at"]).timestamp() < cutoff:
                continue
        except ValueError:
            continue
        adds = [i["playerId"] for i in _items(t["payload"]) if i.get("type") == "ADD"]
        landed = any(p in on_roster or int(p) in points_by_player and any(w > week_of(t["at"]) for w in points_by_player[int(p)])
                     for p in adds)
        claims.append({"at": t["at"][:10], "description": t.get("description"), "landed": landed})
    tables = {}
    for w in range(1, played + 1):
        path = pipeline.analytics_dir(cfg.data_dir, w) / "analysis.json"
        if path.exists():
            try:
                tables[w] = (json.loads(path.read_text(encoding="utf-8")).get("tables") or {}).get("my_roster") or []
            except json.JSONDecodeError:
                continue
    errors = forecast_errors(tables, points_by_player)
    return render(played, bench, claims, trades, errors, ctx.team_name(me))


def write(ctx, cfg):
    path = Path(cfg.data_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(ctx, cfg), encoding="utf-8")
    return path
