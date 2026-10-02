"""
One analytics build: fetch the public data, fit the model, forecast every
player, join the forecasts to this league's rosters and free agents, and
draw the charts. The result is an Analysis the tools read and the report
renders, saved as JSON beside the charts so later runs can read it.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

import gamedaybot.espn.roster as roster
from agent.analytics import model, sources

logger = logging.getLogger(__name__)

SKILL = set(model.SKILL)
CANNOT_PLAY = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
# Doubtful players rarely play; a forecast keeps a quarter of the mean for the chance he does.
DOUBTFUL_FACTOR = 0.25


@dataclass
class Analysis:
    meta: Dict
    tables: Dict[str, pd.DataFrame]
    matchup: Dict
    charts: Dict[str, str] = field(default_factory=dict)
    forecasts: pd.DataFrame = None
    weekly: pd.DataFrame = None

    def to_json(self):
        return {
            "meta": self.meta,
            "matchup": self.matchup,
            "charts": self.charts,
            "tables": {k: json.loads(v.to_json(orient="records")) for k, v in self.tables.items()},
        }


def analytics_dir(data_dir, week):
    return Path(data_dir) / "analytics" / f"week-{int(week):02d}"


def espn_to_gsis(players):
    if players is None or players.empty:
        return {}
    p = players.dropna(subset=["espn_id", "gsis_id"])
    out = {}
    for espn_id, gsis in zip(p["espn_id"], p["gsis_id"]):
        try:
            out[int(float(espn_id))] = gsis
        except (TypeError, ValueError):
            continue
    return out


def end_week_for(league, default=17):
    """Last NFL week that counts: the regular season plus the playoff rounds, capped at 18."""
    try:
        s = league.settings
        reg = int(s.reg_season_count)
        teams = int(s.playoff_team_count or 0)
        length = int(getattr(s, "playoff_matchup_period_length", 1) or 1)
        rounds = max(int(np.ceil(np.log2(teams))), 0) if teams > 1 else 0
        return min(18, reg + rounds * length)
    except Exception:
        return default


def _player_row(entry, team_id, team_name, fc, gsis_map):
    """One league player (rostered or free agent) with the model's numbers joined, or ESPN's when unmodelled."""
    d = entry if isinstance(entry, dict) else entry.to_dict()
    gsis = gsis_map.get(int(d["player_id"])) if d.get("player_id") is not None else None
    row = {
        "player_id": d["player_id"], "name": d["name"], "position": d["position"], "pro_team": d.get("pro_team"),
        "team_id": team_id, "fantasy_team": team_name, "slot": d.get("slot"),
        "is_starter": d.get("slot_id") not in (roster.BENCH_SLOT, roster.IR_SLOT, None) and team_id is not None,
        "eligible": [s for s in (d.get("eligible_slots") or []) if s not in ("BE", "IR")],
        "injury": d.get("injury_status") or "ACTIVE", "bye": bool(d.get("bye")),
        "espn_proj": d.get("projected"), "espn_avg": d.get("season_avg"), "owned": d.get("percent_owned"),
        "status": d.get("status"), "last_week": d.get("last_week"),
        "modelled": False, "mean": None, "p10": None, "p90": None, "ros_pg": None, "ros": None, "ros_games": None,
        "xfp_pg": None, "fpoe_pg": None, "ppg": None, "share": None, "share_trend": None, "snap_pct": None,
        "games": None, "opponent": None, "implied": None, "flags": [],
    }
    if gsis is not None and gsis in fc.index:
        f = fc.loc[gsis]
        mean, p10, p90 = float(f["mean"]), float(f["p10"]), float(f["p90"])
        tag = str(row["injury"]).upper()
        if tag in CANNOT_PLAY or row["bye"]:
            mean = p10 = p90 = 0.0
        elif tag == "DOUBTFUL":
            mean, p10, p90 = mean * DOUBTFUL_FACTOR, 0.0, p90 * DOUBTFUL_FACTOR
        row.update({
            "modelled": True, "mean": round(mean, 1), "p10": round(p10, 1), "p90": round(p90, 1),
            "ros_pg": float(f["ros_pg"]), "ros": float(f["ros"]), "ros_games": int(f["ros_games"]),
            "xfp_pg": float(f["xfp_pg"]), "fpoe_pg": float(f["fpoe_pg"]), "ppg": float(f["ppg"]),
            "share": None if pd.isna(f["share"]) else float(f["share"]),
            "share_trend": float(f["share_trend"]), "snap_pct": None if pd.isna(f["snap_pct"]) else float(f["snap_pct"]),
            "games": int(f["games"]), "opponent": f["opponent"], "implied": f["implied"],
            "flags": model.signals(f.to_dict()),
        })
    return row


def _value(row):
    """The number a lineup decision uses: the model's mean, else ESPN's projection."""
    if row["modelled"]:
        return row["mean"] or 0.0
    return float(row["espn_proj"] or 0.0)


def _sd(row, cvs):
    return model.sd_for(_value(row), row["position"], cvs)


def build(ctx, data_dir, cache_dir=None, draw=True):
    cfg = ctx.cfg
    year = cfg.year
    target_week = int(ctx.week)
    out_dir = analytics_dir(data_dir, target_week)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = sources.load(cache_dir or Path(data_dir) / "analytics" / "cache", year)

    weekly = model.prepare_weekly(src["stats"], src["snaps"], src["players"])
    prior = model.prepare_weekly(src["prior"])
    coefs = model.fit_xfp(pd.concat([prior, weekly], ignore_index=True))
    weekly = model.add_xfp(weekly, coefs)
    prior = model.add_xfp(prior, coefs) if not prior.empty else prior
    cvs = model.position_cv(pd.concat([prior, weekly], ignore_index=True))
    end_week = end_week_for(ctx.league())
    summary = model.summarize(weekly, prior)
    fc = model.forecast(summary, weekly, src["games"], target_week, end_week, cvs)
    fc_idx = fc.set_index("player_id") if not fc.empty else pd.DataFrame()
    gsis_map = espn_to_gsis(src["players"])

    rows = []
    for team_id, entries in ctx.rosters().items():
        name = ctx.team_name(team_id)
        for e in entries:
            rows.append(_player_row(e, team_id, name, fc_idx, gsis_map))
    pool = ctx.free_agents(None, size=150, sort="projected")
    for d in pool:
        rows.append(_player_row(d, None, "Free agent", fc_idx, gsis_map))
    league = pd.DataFrame(rows)

    # Replacement level: the second-best free agent's rest-of-season rate at
    # each position. In an 8-team league that player is always gettable.
    repl = {}
    fa = league[(league["team_id"].isna()) & league["modelled"]]
    for pos in SKILL:
        rates = sorted(fa[fa["position"] == pos]["ros_pg"].dropna(), reverse=True)
        repl[pos] = float(rates[1]) if len(rates) > 1 else (float(rates[0]) if rates else 0.0)
    league["vor_ros"] = [
        round(((r.ros_pg or 0) - repl.get(r.position, 0)) * (r.ros_games or 0), 1) if r.modelled else None
        for r in league.itertuples()]
    # ESPN projects zero for a player it expects to sit; that is news, not a disagreement about talent.
    league["edge"] = [round(r.mean - r.espn_proj, 1) if r.modelled and r.espn_proj else None
                      for r in league.itertuples()]
    # A thin sample (one game, or a backup QB's mop-up snaps) cannot see a
    # role change that ESPN already knows about, so it is flagged and kept
    # out of the disagreement table.
    league["thin"] = [bool(r.modelled and ((r.games or 0) < 2 or (r.position == "QB" and (r.xfp_pg or 0) < 8)))
                      for r in league.itertuples()]
    for i, r in league.iterrows():
        extra = []
        if r["modelled"] and r["espn_proj"] == 0 and not r["bye"]:
            extra.append("ESPN projects 0: likely not expected to play")
        if r["thin"]:
            extra.append("thin sample: model cannot see a new role")
        if extra:
            league.at[i, "flags"] = list(r["flags"]) + extra

    me = cfg.team_id
    mine = league[league["team_id"] == me].copy()
    mine["value"] = [_value(r) for _, r in mine.iterrows()]

    # Matchup: this week's opponent from the box scores.
    opp_id = None
    try:
        for box in ctx.league().box_scores(target_week):
            ids = {box.home_team.team_id: box.away_team, box.away_team.team_id: box.home_team}
            if me in ids and ids[me] is not None:
                opp_id = ids[me].team_id
                break
    except Exception as e:
        logger.warning("box scores unavailable for week %s: %s", target_week, e)
    matchup = {"opponent_id": opp_id, "opponent": ctx.team_name(opp_id) if opp_id else None}
    slot_counts = {roster.slot_name(s): n for s, n in ctx.slot_counts().items()}
    starters = mine[mine["is_starter"]]
    mine_dist = [(_value(r), _sd(r, cvs)) for _, r in starters.iterrows()]
    best = model.best_lineup([dict(r, value=_value(r)) for _, r in mine.iterrows()
                              if str(r["injury"]).upper() not in CANNOT_PLAY and not r["bye"]], slot_counts)
    best_dist = [(p["value"], model.sd_for(p["value"], p["position"], cvs)) for _, p in best]
    matchup.update({
        "my_mean": round(sum(m for m, _ in mine_dist), 1), "my_sd": round(float(np.sqrt(sum(s * s for _, s in mine_dist))), 1),
        "best_mean": round(sum(m for m, _ in best_dist), 1),
        "best_lineup": [{"slot": s, "name": p["name"], "value": round(p["value"], 1)} for s, p in best],
        "current_starters": [{"slot": r["slot"], "name": r["name"], "value": round(_value(r), 1)} for _, r in starters.iterrows()],
    })
    best_names = {p["name"] for _, p in best}
    cur_names = set(starters["name"])
    matchup["swaps_in"] = sorted(best_names - cur_names)
    matchup["swaps_out"] = sorted(cur_names - best_names)
    if opp_id is not None:
        opp = league[(league["team_id"] == opp_id) & league["is_starter"]]
        opp_dist = [(_value(r), _sd(r, cvs)) for _, r in opp.iterrows()]
        matchup.update({
            "opp_mean": round(sum(m for m, _ in opp_dist), 1),
            "opp_sd": round(float(np.sqrt(sum(s * s for _, s in opp_dist))), 1),
            "win_prob": round(model.win_probability(mine_dist, opp_dist), 3),
            "win_prob_best": round(model.win_probability(best_dist, opp_dist), 3),
        })
        opp_table = opp[["slot", "name", "position", "pro_team", "espn_proj", "mean", "p10", "p90", "injury"]].copy()
    else:
        opp_table = pd.DataFrame()

    cols = ["name", "position", "pro_team", "slot", "injury", "espn_proj", "mean", "p10", "p90", "edge", "ros_pg",
            "espn_avg", "vor_ros", "xfp_pg", "fpoe_pg", "share", "share_trend", "snap_pct", "opponent", "implied",
            "modelled", "flags", "player_id"]
    tables = {}
    tables["my_roster"] = mine.sort_values(["is_starter", "value"], ascending=[False, False])[cols].reset_index(drop=True)

    fa_cols = ["name", "position", "pro_team", "status", "owned", "espn_proj", "mean", "p10", "p90", "edge",
               "ros_pg", "vor_ros", "last_week", "xfp_pg", "fpoe_pg", "share", "share_trend", "modelled", "flags",
              "player_id"]
    fa_all = league[league["team_id"].isna()]
    # The best few at each position, ordered by value over replacement, so one
    # deep position (QB, in an 8-team league) cannot crowd out the rest.
    skill_fa = (fa_all[fa_all["modelled"]].sort_values("ros_pg", ascending=False)
                .groupby("position").head(4).sort_values("vor_ros", ascending=False))
    kd = fa_all[fa_all["position"].isin(["K", "D/ST"])].sort_values("espn_proj", ascending=False).groupby("position").head(3)
    tables["waiver"] = pd.concat([skill_fa, kd])[fa_cols].reset_index(drop=True)

    others = league[league["team_id"].notna() & (league["team_id"] != me) & league["modelled"]].copy()
    others["market_gap"] = others["ros_pg"] - others["espn_avg"].fillna(0)
    targets = others[(others["market_gap"] >= 1.0) | others["flags"].map(lambda f: any("buy-low" in x for x in f))]
    t_cols = ["name", "position", "pro_team", "fantasy_team", "team_id", "espn_avg", "ros_pg", "market_gap",
              "vor_ros", "xfp_pg", "fpoe_pg", "flags", "player_id"]
    tables["trade_targets"] = targets.sort_values("vor_ros", ascending=False).head(12)[t_cols].reset_index(drop=True)

    my_mod = mine[mine["modelled"]].copy()
    my_mod["market_gap"] = my_mod["ros_pg"] - my_mod["espn_avg"].fillna(0)
    sells = my_mod[(my_mod["market_gap"] <= -2.0) | my_mod["flags"].map(lambda f: any("sell-high" in x for x in f))]
    tables["sell_candidates"] = sells.sort_values("market_gap")[
        ["name", "position", "espn_avg", "ros_pg", "market_gap", "xfp_pg", "fpoe_pg", "flags", "player_id"]].reset_index(drop=True)

    relevant = league[(league["team_id"] == me) | (league["team_id"].isna()) | (league["team_id"] == opp_id)]
    div = relevant[relevant["edge"].notna() & ~relevant["thin"]].copy()
    div = div[(div[["mean", "espn_proj"]].max(axis=1) >= 6)]
    div["abs_edge"] = div["edge"].abs()
    tables["divergences"] = div.sort_values("abs_edge", ascending=False).head(12)[
        ["name", "position", "fantasy_team", "espn_proj", "mean", "edge", "xfp_pg", "fpoe_pg", "opponent", "implied", "flags"]
    ].reset_index(drop=True)
    tables["opponent"] = opp_table.reset_index(drop=True)

    data_weeks = sorted(int(w) for w in weekly["week"].unique()) if not weekly.empty else []
    meta = {
        "year": year, "target_week": target_week, "end_week": end_week, "data_weeks": data_weeks,
        "data_through": data_weeks[-1] if data_weeks else None,
        "teams_in_last_week": int(weekly[weekly["week"] == data_weeks[-1]]["team"].nunique()) if data_weeks else 0,
        "missing_sources": src["missing"], "coefs": coefs, "cv": cvs, "replacement_ros_pg": repl,
        "players_modelled": int(len(fc)), "team": ctx.team_name(me), "team_id": me,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
    }
    analysis = Analysis(meta=meta, tables=tables, matchup=matchup, forecasts=fc, weekly=weekly)
    if draw:
        from agent.analytics import charts
        try:
            analysis.charts = charts.draw_all(analysis, mine, league, out_dir)
        except Exception:
            logger.exception("chart drawing failed")
    (out_dir / "analysis.json").write_text(json.dumps(analysis.to_json(), indent=1, default=str), encoding="utf-8")
    return analysis


# ------------------------------------------------------------------ text views

DIGEST_COLUMNS = {
    "my_roster": ["name", "position", "slot", "injury", "espn_proj", "mean", "p10", "p90", "ros_pg", "espn_avg",
                  "vor_ros", "xfp_pg", "fpoe_pg", "share", "share_trend", "snap_pct", "opponent", "implied", "flags"],
    "waiver": ["name", "position", "pro_team", "status", "owned", "espn_proj", "mean", "p10", "p90", "ros_pg",
               "vor_ros", "xfp_pg", "fpoe_pg", "share", "share_trend", "player_id", "flags"],
    "trade_targets": ["name", "position", "fantasy_team", "team_id", "espn_avg", "ros_pg", "market_gap", "vor_ros",
                      "xfp_pg", "fpoe_pg", "player_id", "flags"],
    "sell_candidates": ["name", "position", "espn_avg", "ros_pg", "market_gap", "xfp_pg", "fpoe_pg", "flags"],
    "divergences": ["name", "position", "fantasy_team", "espn_proj", "mean", "edge", "xfp_pg", "fpoe_pg",
                    "opponent", "implied", "flags"],
    "opponent": ["slot", "name", "position", "espn_proj", "mean", "p10", "p90", "injury"],
}
DIGEST_NOTES = {
    "my_roster": "mean/p10/p90 = this week's forecast and 10-90% range; ros_pg = rest-of-season points per game; "
                 "espn_avg = actual PPG so far; vor_ros = rest-of-season points over a replacement free agent; "
                 "xfp_pg = expected points per game from usage; fpoe_pg = points over expectation per game; "
                 "share = opportunity share; share_trend = change in share per week",
    "trade_targets": "players on other teams whose usage says they are worth more than their production so far "
                     "(market_gap = ros_pg - espn_avg), ordered by value over replacement",
    "sell_candidates": "my players producing above what their usage supports",
    "divergences": "edge = model minus ESPN this week; thin samples left out",
}


def _flat(df, cols):
    if df is None or df.empty:
        return "(none)"
    d = df[[c for c in cols if c in df.columns]].copy()
    if "flags" in d.columns:
        d["flags"] = d["flags"].map(lambda f: "; ".join(f) if f else "")
    for c in ("share", "snap_pct"):
        if c in d.columns:
            d[c] = d[c].map(lambda v: "" if v is None or pd.isna(v) else f"{v * 100:.0f}%")
    with pd.option_context("display.width", 400, "display.max_columns", 40, "display.max_colwidth", 70):
        return d.to_string(index=False, na_rep="-")


def digest(analysis):
    """Everything the analyst needs to write the report, as plain text."""
    m, meta = analysis.matchup, analysis.meta
    lines = [
        f"Analytics for {meta['team']} (team {meta['team_id']}), NFL week {meta['target_week']}; data through "
        f"week {meta['data_through']} ({meta['teams_in_last_week']} teams have that week on file). "
        f"Rest of season through week {meta['end_week']}. {meta['players_modelled']} players modelled.",
    ]
    if meta.get("missing_sources"):
        lines.append(f"MISSING SOURCES: {', '.join(meta['missing_sources'])}; say so in the report.")
    fit = "; ".join(f"{p} R2 {c.get('r2', 0):.2f}" for p, c in meta["coefs"].items() if "r2" in c)
    lines.append(f"xFP fit: {fit or 'default weights'}. Replacement ROS/g: "
                 + ", ".join(f"{k} {v:.1f}" for k, v in meta["replacement_ros_pg"].items()))
    if m.get("win_prob") is not None:
        lines.append(f"\nMatchup vs {m['opponent']} (team {m['opponent_id']}): me {m['my_mean']} ± {m['my_sd']}, "
                     f"them {m['opp_mean']} ± {m['opp_sd']}; win probability {m['win_prob']:.0%} as set, "
                     f"{m['win_prob_best']:.0%} with the model's best lineup ({m['best_mean']}).")
        if m.get("swaps_in"):
            lines.append(f"Model's lineup would start {', '.join(m['swaps_in'])} over {', '.join(m['swaps_out'])}.")
    for key, cols in DIGEST_COLUMNS.items():
        lines.append(f"\n## {key}" + (f"  ({DIGEST_NOTES[key]})" if key in DIGEST_NOTES else ""))
        lines.append(_flat(analysis.tables.get(key), cols))
    lines.append("\nCharts drawn: " + (", ".join(analysis.charts) or "none"))
    return "\n".join(lines)


def player_trend(analysis, name):
    """Week-by-week usage and the forecast row for players whose name contains `name`."""
    w, fc = analysis.weekly, analysis.forecasts
    if w is None or w.empty:
        return None
    hits = w[w["name"].str.contains(str(name), case=False, regex=False, na=False)]
    if hits.empty:
        return None
    out = []
    for pid, g in hits.groupby("player_id"):
        g = g.sort_values("week")
        cols = ["week", "team", "opponent", "snap_pct", "targets", "carries", "receptions", "receiving_air_yards",
                "attempts", "target_share", "rush_share", "opp_share", "xfp", "points", "fpoe"]
        with pd.option_context("display.width", 300, "display.max_columns", 30):
            out.append(f"{g['name'].iloc[-1]} ({g['position'].iloc[-1]}, {g['team'].iloc[-1]})\n"
                       + g[[c for c in cols if c in g.columns]].round(3).to_string(index=False))
        if fc is not None and not fc.empty and pid in set(fc["player_id"]):
            row = fc[fc["player_id"] == pid].iloc[0].to_dict()
            keep = ["games", "ppg", "xfp_pg", "fpoe_pg", "base", "efficiency", "prior_xfp_pg", "share", "share_trend",
                    "snap_pct", "mean", "p10", "p90", "opponent", "implied", "ros", "ros_pg", "ros_games"]
            out.append("forecast: " + ", ".join(f"{k}={row.get(k)}" for k in keep))
    return "\n\n".join(out)


def research_tables(analysis):
    """The compact tables that ride along in the research file under the analyst's own summary."""
    t = analysis.tables
    mine = t["my_roster"]
    parts = ["**Model vs ESPN, my roster (this week)**", "",
             "| Player | Slot | ESPN | Model | 10-90% | ROS/g | Signals |", "|:--|:--|--:|--:|:--|--:|:--|"]
    for _, r in mine.iterrows():
        rng = f"{r['p10']:.0f}-{r['p90']:.0f}" if r["modelled"] else "-"
        model_v = f"{r['mean']:.1f}" if r["modelled"] else "-"
        ros = f"{r['ros_pg']:.1f}" if r["modelled"] else "-"
        espn = f"{r['espn_proj']:.1f}" if r["espn_proj"] is not None and not pd.isna(r["espn_proj"]) else "-"
        parts.append(f"| {r['name']} | {r['slot']} | {espn} | {model_v} | {rng} | {ros} | {'; '.join(r['flags'])} |")
    fa = t["waiver"][t["waiver"]["modelled"]].head(8)
    parts += ["", "**Best free agents by the model**", "",
              "| Player | Pos | Status | ESPN | Model | ROS/g | VOR | Signals |", "|:--|:--|:--|--:|--:|--:|--:|:--|"]
    for _, r in fa.iterrows():
        parts.append(f"| {r['name']} | {r['position']} | {r['status']} | {r['espn_proj']:.1f} | {r['mean']:.1f} | "
                     f"{r['ros_pg']:.1f} | {r['vor_ros']:.0f} | {'; '.join(r['flags'])} |")
    return "\n".join(parts)
