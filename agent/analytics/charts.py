"""
The report's figures, drawn with matplotlib for print.

Colors follow the dataviz reference palette on a light surface: blue is the
in-house model, orange is ESPN, aqua is a third identity where one is needed,
and the diverging pair is blue (model above ESPN) against red (below). Text
stays in ink colors; marks carry identity. Every chart with two series has a
legend, and the key values are labelled directly because a PDF has no hover.
"""
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e6e5e1"
SURFACE = "#ffffff"
BLUE = "#2a78d6"
BLUE_LIGHT = "#b7d3f6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
RED = "#e34948"
NEUTRAL = "#c9c8c3"
DPI = 170

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8.5, "axes.edgecolor": GRID, "axes.labelcolor": INK_2,
    "axes.titlecolor": INK, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "xtick.color": INK_2, "ytick.color": INK_2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "legend.frameon": False, "legend.fontsize": 8,
})


def _save(fig, path):
    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return str(path)


def range_chart(rows, title, path, label_fn=None):
    """
    One row per player: the model's 10-90% range as a band, its forecast as a
    dot, and ESPN's projection as a diamond. rows: dicts with name, mean, p10,
    p90, espn_proj, modelled.
    """
    rows = [r for r in rows if (r.get("mean") is not None or r.get("espn_proj") is not None)]
    if not rows:
        return None
    rows = rows[::-1]
    h = max(2.2, 0.27 * len(rows) + 0.9)
    fig, ax = plt.subplots(figsize=(7.0, h))
    ys = np.arange(len(rows))
    for y, r in zip(ys, rows):
        if r.get("modelled"):
            ax.plot([r["p10"], r["p90"]], [y, y], color=BLUE_LIGHT, linewidth=7, solid_capstyle="round", zorder=1)
            ax.plot(r["mean"], y, "o", color=BLUE, markersize=6.5, markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=3)
            ax.annotate(f"{r['mean']:.1f}", (r["p90"], y), xytext=(5, 0), textcoords="offset points",
                        va="center", fontsize=7.5, color=INK_2)
        if r.get("espn_proj") is not None:
            ax.plot(r["espn_proj"], y, "D", color=ORANGE, markersize=5, markeredgecolor=SURFACE,
                    markeredgewidth=1.2, zorder=4)
    ax.set_yticks(ys)
    ax.set_yticklabels([label_fn(r) if label_fn else r["name"] for r in rows], color=INK)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(left=0)
    ax.set_xlabel("PPR points this week")
    ax.set_title(title)
    handles = [plt.Line2D([], [], marker="o", color=BLUE, linestyle="", markersize=6.5, label="In-house forecast"),
               plt.Line2D([], [], color=BLUE_LIGHT, linewidth=7, label="10-90% range"),
               plt.Line2D([], [], marker="D", color=ORANGE, linestyle="", markersize=5, label="ESPN projection")]
    ax.legend(handles=handles, loc="upper center", ncol=3, bbox_to_anchor=(0.5, -0.09 - 0.9 / h * 0.25))
    return _save(fig, path)


def usage_trends(weekly, players, path, max_panels=9):
    """Small multiples: each player's share of his team's opportunities by week."""
    panels = []
    for p in players:
        g = weekly[weekly["player_id"] == p["gsis"]].sort_values("week")
        if len(g) >= 2 and g["opp_share"].notna().any():
            panels.append((p, g))
        if len(panels) >= max_panels:
            break
    if not panels:
        return None
    cols = 3
    rows = math.ceil(len(panels) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(7.0, 1.9 * rows + 0.4), sharey=True, squeeze=False)
    weeks = sorted(weekly["week"].unique())
    for ax in axes.flat[len(panels):]:
        ax.set_visible(False)
    for ax, (p, g) in zip(axes.flat, panels):
        share = g["opp_share"] * 100
        ax.plot(g["week"], share, color=BLUE, linewidth=2, marker="o", markersize=4.5,
                markeredgecolor=SURFACE, markeredgewidth=1)
        last = g.iloc[-1]
        ax.annotate(f"{last['opp_share'] * 100:.0f}%", (last["week"], last["opp_share"] * 100), xytext=(4, 4),
                    textcoords="offset points", fontsize=7.5, color=INK)
        if g["snap_pct"].notna().any():
            ax.plot(g["week"], g["snap_pct"] * 100, color=MUTED, linewidth=1, linestyle="--")
        ax.set_title(f"{p['name']} ({p['position']})", fontsize=8.5)
        ax.set_xticks(weeks)
        ax.set_ylim(0, 100)
    fig.supylabel("% of team", color=INK_2, fontsize=8)
    fig.supxlabel("NFL week", color=INK_2, fontsize=8)
    handles = [plt.Line2D([], [], color=BLUE, linewidth=2, marker="o", markersize=4.5,
                          label="Opportunity share (WR/TE: targets; RB: half carries, half targets)"),
               plt.Line2D([], [], color=MUTED, linewidth=1, linestyle="--", label="Offensive snap share")]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.0, 1.04), ncol=1)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    return _save(fig, path)


def luck_scatter(league, my_team_id, target_ids, path):
    """Expected points per game against actual: above the line is running hot, below is running cold."""
    d = league[league["modelled"] & (league["games"].fillna(0) >= 2) & league["position"].isin(["RB", "WR", "TE"])]
    if d.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    others = d[(d["team_id"] != my_team_id) & ~d["player_id"].isin(target_ids)]
    ax.scatter(others["xfp_pg"], others["ppg"], s=16, color=NEUTRAL, edgecolors=SURFACE, linewidths=0.6,
               label="Other rostered and free-agent RB/WR/TE", zorder=2)
    tg = d[d["player_id"].isin(target_ids)]
    ax.scatter(tg["xfp_pg"], tg["ppg"], s=34, color=ORANGE, edgecolors=SURFACE, linewidths=1.2,
               label="Trade targets", zorder=3)
    mine = d[d["team_id"] == my_team_id]
    ax.scatter(mine["xfp_pg"], mine["ppg"], s=40, color=BLUE, edgecolors=SURFACE, linewidths=1.2,
               label="My players", zorder=4)
    hi = float(max(d["xfp_pg"].max(), d["ppg"].max()) * 1.05)
    ax.plot([0, hi], [0, hi], color=MUTED, linewidth=1, linestyle="--", zorder=1)
    ax.text(hi * 0.98, hi * 0.9, "production = usage", color=MUTED, fontsize=7.5, ha="right", rotation=0)
    ax.set_xlim(0, hi)
    ax.set_ylim(0, hi)
    labelled = pd.concat([mine, tg.sort_values("vor_ros", ascending=False).head(6)])
    _place_labels(ax, labelled, hi)
    ax.set_xlabel("Expected PPR points per game from usage (xFP)")
    ax.set_ylabel("Actual PPR points per game")
    ax.set_title("Usage against production")
    ax.legend(loc="upper left")
    return _save(fig, path)


def _place_labels(ax, rows, hi):
    """Surname labels beside their points, nudged vertically until no two overlap."""
    min_gap = hi * 0.032
    placed = []
    for _, r in rows.sort_values("ppg", ascending=False).iterrows():
        x, y = float(r["xfp_pg"]), float(r["ppg"])
        ty = y
        for _ in range(40):
            clash = [p for p in placed if abs(p[0] - x) < hi * 0.14 and abs(p[1] - ty) < min_gap]
            if not clash:
                break
            ty = min(p[1] for p in clash) - min_gap
        placed.append((x, ty))
        ax.annotate(r["name"].split(" ", 1)[-1], (x, y), xytext=(x + hi * 0.012, ty), textcoords="data",
                    fontsize=7, color=INK, va="center",
                    arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.5) if abs(ty - y) > 0.3 else None)


def matchup_chart(matchup, team, path):
    if matchup.get("opp_mean") is None:
        return None
    m1, s1, m2, s2 = matchup["my_mean"], max(matchup["my_sd"], 1), matchup["opp_mean"], max(matchup["opp_sd"], 1)
    lo = min(m1 - 3.2 * s1, m2 - 3.2 * s2)
    hi = max(m1 + 3.2 * s1, m2 + 3.2 * s2)
    x = np.linspace(max(lo, 0), hi, 400)

    def pdf(m, s):
        return np.exp(-0.5 * ((x - m) / s) ** 2) / (s * math.sqrt(2 * math.pi))

    fig, ax = plt.subplots(figsize=(7.0, 2.8))
    ax.fill_between(x, pdf(m1, s1), color=BLUE, alpha=0.12, linewidth=0)
    ax.plot(x, pdf(m1, s1), color=BLUE, linewidth=2, label=f"{team}: {m1:.0f} ± {s1:.0f}")
    ax.plot(x, pdf(m2, s2), color=ORANGE, linewidth=2, label=f"{matchup['opponent'].strip()}: {m2:.0f} ± {s2:.0f}")
    top = max(pdf(m1, s1).max(), pdf(m2, s2).max())
    for m, c in ((m1, BLUE), (m2, ORANGE)):
        ax.plot([m, m], [0, top * 1.04], color=c, linewidth=1, linestyle=":")
    ax.set_yticks([])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Team PPR points, current lineups")
    ax.set_title(f"This week: {matchup['win_prob'] * 100:.0f}% to win as the lineups stand")
    ax.legend(loc="upper right")
    return _save(fig, path)


def divergence_chart(rows, path):
    rows = [r for r in rows if r.get("edge") is not None]
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r["edge"])
    fig, ax = plt.subplots(figsize=(7.0, max(2.2, 0.27 * len(rows) + 0.9)))
    ys = np.arange(len(rows))
    colors = [BLUE if r["edge"] > 0 else RED for r in rows]
    ax.barh(ys, [r["edge"] for r in rows], color=colors, height=0.62, edgecolor=SURFACE, linewidth=1)
    for y, r in zip(ys, rows):
        ax.annotate(f"{r['edge']:+.1f}", (r["edge"], y), xytext=(4 if r["edge"] > 0 else -4, 0),
                    textcoords="offset points", va="center", ha="left" if r["edge"] > 0 else "right",
                    fontsize=7.5, color=INK_2)
    ax.axvline(0, color=INK_2, linewidth=0.8)
    ax.set_yticks(ys)
    ax.set_yticklabels([f"{r['name']} ({r['position']})" for r in rows], color=INK)
    ax.grid(axis="y", visible=False)
    lim = max(abs(r["edge"]) for r in rows) * 1.3
    ax.set_xlim(-lim, lim)
    ax.set_xlabel("In-house forecast minus ESPN projection (PPR points)")
    ax.set_title("Where the model disagrees with ESPN this week")
    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE, label="Model higher"),
               plt.Rectangle((0, 0), 1, 1, color=RED, label="Model lower")]
    ax.legend(handles=handles, loc="lower right")
    return _save(fig, path)


def draw_all(analysis, mine, league, out_dir):
    out_dir = Path(out_dir)
    t = analysis.tables
    charts = {}
    my_rows = t["my_roster"].to_dict("records")
    p = range_chart(my_rows, "My roster: this week's forecast", out_dir / "roster_ranges.png",
                    label_fn=lambda r: f"{r['name']}  ·  {r['slot']}")
    if p:
        charts["roster_ranges"] = p
    fa_rows = [r for r in t["waiver"].to_dict("records") if r.get("mean") is not None][:12]
    p = range_chart(fa_rows, "Free agents: this week's forecast", out_dir / "waiver_ranges.png",
                    label_fn=lambda r: f"{r['name']}  ·  {r['position']} {r['pro_team']}")
    if p:
        charts["waiver_ranges"] = p
    trend_players = []
    by_name = {}
    if analysis.weekly is not None and not analysis.weekly.empty:
        by_name = dict(zip(analysis.weekly["name"], analysis.weekly["player_id"]))
    for r in sorted(my_rows, key=lambda r: -(r.get("mean") or 0)):
        if r["position"] in ("RB", "WR", "TE") and r["name"] in by_name:
            trend_players.append({"name": r["name"], "position": r["position"], "gsis": by_name[r["name"]]})
    p = usage_trends(analysis.weekly, trend_players, out_dir / "usage_trends.png")
    if p:
        charts["usage_trends"] = p
    target_ids = list(t["trade_targets"]["player_id"]) if not t["trade_targets"].empty else []
    p = luck_scatter(league, analysis.meta["team_id"], target_ids, out_dir / "luck_scatter.png")
    if p:
        charts["luck_scatter"] = p
    p = matchup_chart(analysis.matchup, analysis.meta["team"], out_dir / "matchup.png")
    if p:
        charts["matchup"] = p
    p = divergence_chart(t["divergences"].to_dict("records"), out_dir / "divergence.png")
    if p:
        charts["divergence"] = p
    return charts
