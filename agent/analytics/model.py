"""
The analyst's model, as pure functions over DataFrames.

The idea: usage is sticky and efficiency is noisy. So each player-week gets
an expected PPR score from what he was given (targets, air yards, carries,
pass attempts), fitted by position on this season and last. A forecast
starts from a recency-weighted average of that expected score, blends in last
season as a prior while the sample is small, adds back only a heavily shrunk
share of the player's points over expectation, and scales by the game
environment: the Vegas implied team total and how many points the opponent
allows to the position. Ranges come from a lognormal with the position's
week-to-week spread, because fantasy scores are skewed right.

Points are nflverse's PPR scoring, which is ESPN's full-PPR default to within
rounding. Kickers and D/STs are not modelled.
"""
import math

import numpy as np
import pandas as pd

SKILL = ("QB", "RB", "WR", "TE")

# Expected-points features by position. Opportunity, not outcome: no yards or
# touchdowns on the right-hand side, except QB rushing attempts.
FEATURES = {
    "QB": ["attempts", "passing_air_yards", "carries"],
    "RB": ["carries", "targets"],
    "WR": ["targets", "receiving_air_yards"],
    "TE": ["targets", "receiving_air_yards"],
}
# Used when a position has too few rows to fit (early season, a failed download).
DEFAULT_COEFS = {
    "QB": {"const": 2.0, "attempts": 0.30, "passing_air_yards": 0.022, "carries": 0.75},
    "RB": {"const": 0.8, "carries": 0.62, "targets": 1.55},
    "WR": {"const": 0.4, "targets": 1.45, "receiving_air_yards": 0.035},
    "TE": {"const": 0.4, "targets": 1.40, "receiving_air_yards": 0.030},
}
DEFAULT_CV = {"QB": 0.40, "RB": 0.55, "WR": 0.60, "TE": 0.65}

HALFLIFE_WEEKS = 2.0      # recency weight halves every two weeks
PRIOR_GAMES = 2.0         # last season counts as this many games of evidence
EFFICIENCY_SHRINK = 6.0   # pseudo-games of zero over-expectation added to the efficiency mean
DVP_SHRINK = 4.0          # pseudo-games of league-average defense
Z90 = 1.2816


def _num(df, cols):
    for c in cols:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def prepare_weekly(stats, snaps=None, players=None):
    """
    One row per skill player-week of the regular season with usage columns,
    team rush share, and snap share where the snap file can be joined.
    """
    if stats is None or stats.empty:
        return pd.DataFrame(columns=["player_id", "name", "position", "team", "opponent", "week", "points"])
    df = stats.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"].astype(str) == "REG"]
    numeric = ["attempts", "passing_air_yards", "carries", "targets", "receptions", "receiving_air_yards",
               "target_share", "air_yards_share", "wopr", "fantasy_points_ppr", "rushing_yards",
               "receiving_yards", "passing_yards"]
    df = _num(df, numeric)
    team_carries = df.groupby(["season", "week", "team"])["carries"].transform("sum")
    df = df[df["position"].isin(SKILL)].copy()
    df["rush_share"] = np.where(team_carries.loc[df.index] > 0, df["carries"] / team_carries.loc[df.index], 0.0)
    df = df.rename(columns={"player_display_name": "name", "opponent_team": "opponent",
                            "fantasy_points_ppr": "points"})
    df["snap_pct"] = np.nan
    if snaps is not None and not snaps.empty and players is not None and not players.empty:
        pfr = players.dropna(subset=["pfr_id", "gsis_id"]).set_index("gsis_id")["pfr_id"]
        s = snaps.copy()
        if "game_type" in s.columns:
            s = s[s["game_type"].astype(str) == "REG"]
        s = s.groupby(["pfr_player_id", "week"], as_index=False)["offense_pct"].max()
        df["pfr_id"] = df["player_id"].map(pfr)
        df = df.merge(s.rename(columns={"pfr_player_id": "pfr_id", "offense_pct": "_snap"}),
                      on=["pfr_id", "week"], how="left")
        df["snap_pct"] = df["_snap"]
        df = df.drop(columns=["_snap", "pfr_id"])
    # Opportunity share: what fraction of the team's looks went to him.
    df["opp_share"] = np.where(df["position"] == "RB", 0.5 * df["rush_share"] + 0.5 * df["target_share"],
                               np.where(df["position"] == "QB", np.nan, df["target_share"]))
    keep = ["player_id", "name", "position", "season", "week", "team", "opponent", "attempts", "passing_air_yards",
            "carries", "targets", "receptions", "receiving_air_yards", "target_share", "air_yards_share", "wopr",
            "rush_share", "opp_share", "snap_pct", "points"]
    return df[[c for c in keep if c in df.columns]].reset_index(drop=True)


def fit_xfp(weekly, min_rows=40):
    """Least-squares coefficients per position: points ~ const + opportunity features."""
    coefs = {}
    for pos, feats in FEATURES.items():
        rows = weekly[(weekly["position"] == pos)] if not weekly.empty else weekly
        rows = rows[rows[feats].sum(axis=1) > 0] if len(rows) else rows
        if len(rows) < min_rows:
            coefs[pos] = dict(DEFAULT_COEFS[pos])
            continue
        X = np.column_stack([np.ones(len(rows))] + [rows[f].to_numpy(float) for f in feats])
        y = rows["points"].to_numpy(float)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        fitted = {"const": float(beta[0])}
        for f, b in zip(feats, beta[1:]):
            # A negative weight on an opportunity is noise in a small sample; fall back.
            fitted[f] = float(b) if b > 0 else DEFAULT_COEFS[pos][f]
        fitted["r2"] = float(1 - np.sum((y - X @ beta) ** 2) / max(np.sum((y - y.mean()) ** 2), 1e-9))
        fitted["n"] = int(len(rows))
        coefs[pos] = fitted
    return coefs


def add_xfp(weekly, coefs):
    df = weekly.copy()
    df["xfp"] = 0.0
    for pos, feats in FEATURES.items():
        m = df["position"] == pos
        if not m.any():
            continue
        c = coefs.get(pos, DEFAULT_COEFS[pos])
        val = c.get("const", 0.0) + sum(c.get(f, 0.0) * df.loc[m, f] for f in feats)
        df.loc[m, "xfp"] = np.maximum(val, 0.0)
    df["fpoe"] = df["points"] - df["xfp"]
    return df


def position_cv(weekly, min_mean=8.0):
    """Week-to-week spread relative to the mean, per position, from players with a real role."""
    out = dict(DEFAULT_CV)
    if weekly.empty:
        return out
    g = weekly.groupby(["player_id", "position"])["points"].agg(["mean", "std", "count"]).reset_index()
    g = g[(g["count"] >= 3) & (g["mean"] >= min_mean)]
    for pos, rows in g.groupby("position"):
        if len(rows) >= 8:
            out[pos] = float(np.clip((rows["std"] / rows["mean"]).median(), 0.25, 0.9))
    return out


# ------------------------------------------------------------------ schedule


def team_weeks(games):
    """{(team, week): {"opponent", "home", "implied"}} from the schedule, with Vegas implied totals where posted."""
    out = {}
    if games is None or games.empty:
        return out
    g = games.copy()
    if "game_type" in g.columns:
        g = g[g["game_type"].astype(str) == "REG"]
    for r in g.itertuples():
        total = getattr(r, "total_line", np.nan)
        spread = getattr(r, "spread_line", np.nan)   # positive: home favoured
        home_imp = away_imp = np.nan
        if pd.notna(total) and pd.notna(spread):
            home_imp = (float(total) + float(spread)) / 2
            away_imp = (float(total) - float(spread)) / 2
        week = int(r.week)
        out[(r.home_team, week)] = {"opponent": r.away_team, "home": True, "implied": home_imp}
        out[(r.away_team, week)] = {"opponent": r.home_team, "home": False, "implied": away_imp}
    return out


def defense_factors(weekly):
    """
    {(defense, position): factor}: points allowed to the position per game
    relative to the league, shrunk toward 1 by games played.
    """
    if weekly.empty:
        return {}
    per_game = weekly.groupby(["opponent", "position", "week"])["points"].sum().reset_index()
    league = per_game.groupby("position")["points"].mean()
    agg = per_game.groupby(["opponent", "position"])["points"].agg(["mean", "count"]).reset_index()
    out = {}
    for r in agg.itertuples():
        base = league.get(r.position, 0)
        if not base:
            continue
        ratio = r.mean / base
        w = r.count / (r.count + DVP_SHRINK)
        out[(r.opponent, r.position)] = 1 + (ratio - 1) * w
    return out


def environment(team, position, week, sched, dvp, avg_implied):
    """(multiplier, opponent, implied total). A bye returns multiplier 0."""
    game = sched.get((team, week))
    if game is None:
        return 0.0, None, None
    implied = game["implied"]
    team_f = (implied / avg_implied) if (implied == implied and implied and avg_implied) else 1.0
    def_f = dvp.get((game["opponent"], position), 1.0)
    mult = float(np.clip(team_f ** 0.6 * def_f ** 0.35, 0.75, 1.3))
    return mult, game["opponent"], (None if implied != implied else round(implied, 1))


def _avg_implied(sched, week):
    vals = [v["implied"] for (t, w), v in sched.items() if w == week and v["implied"] == v["implied"]]
    return float(np.mean(vals)) if vals else None


def lognormal_range(mean, cv):
    """(p10, p90) of a lognormal with this mean and coefficient of variation."""
    if mean <= 0:
        return 0.0, 0.0
    s2 = math.log(1 + cv * cv)
    mu = math.log(mean) - s2 / 2
    s = math.sqrt(s2)
    return math.exp(mu - Z90 * s), math.exp(mu + Z90 * s)


# ------------------------------------------------------------------ forecast


def _slope(weeks, values):
    if len(values) < 3:
        return 0.0
    w = np.asarray(weeks, float)
    v = np.asarray(values, float)
    if np.all(np.isnan(v)):
        return 0.0
    m = ~np.isnan(v)
    if m.sum() < 3:
        return 0.0
    return float(np.polyfit(w[m], v[m], 1)[0])


def summarize(weekly, prior=None):
    """One row per player: season usage, efficiency, trend, and last season's per-game prior."""
    if weekly.empty:
        return pd.DataFrame()
    last_week = int(weekly["week"].max())
    rows = []
    prior_pg = {}
    if prior is not None and not prior.empty:
        pg = prior.groupby("player_id").agg(games=("week", "nunique"), xfp=("xfp", "mean"), fpoe=("fpoe", "mean"))
        prior_pg = {pid: r for pid, r in pg.iterrows() if r["games"] >= 4}
    for pid, g in weekly.sort_values("week").groupby("player_id"):
        g = g[(g["xfp"] > 0) | (g["points"] != 0) | (g["snap_pct"].fillna(0) > 0)]
        if g.empty:
            continue
        weeks = g["week"].to_numpy()
        wts = 0.5 ** ((last_week - weeks) / HALFLIFE_WEEKS)
        ewma_xfp = float(np.sum(wts * g["xfp"]) / np.sum(wts))
        n_eff = float(np.sum(wts))
        p = prior_pg.get(pid)
        if p is not None:
            base = (n_eff * ewma_xfp + PRIOR_GAMES * p["xfp"]) / (n_eff + PRIOR_GAMES)
            eff = (g["fpoe"].sum() + PRIOR_GAMES * p["fpoe"]) / (len(g) + PRIOR_GAMES + EFFICIENCY_SHRINK)
        else:
            base = ewma_xfp
            eff = g["fpoe"].sum() / (len(g) + EFFICIENCY_SHRINK)
        last = g.iloc[-1]
        share = g["opp_share"] if "opp_share" in g else pd.Series(dtype=float)
        rows.append({
            "player_id": pid, "name": last["name"], "position": last["position"], "team": last["team"],
            "games": int(len(g)), "last_played": int(last["week"]),
            "ppg": round(float(g["points"].mean()), 2), "xfp_pg": round(float(g["xfp"].mean()), 2),
            "fpoe_pg": round(float(g["fpoe"].mean()), 2),
            "xfp_recent": round(ewma_xfp, 2), "base": round(float(base), 2), "efficiency": round(float(eff), 2),
            "prior_xfp_pg": None if p is None else round(float(p["xfp"]), 2),
            "targets_pg": round(float(g["targets"].mean()), 1), "carries_pg": round(float(g["carries"].mean()), 1),
            "share": None if share.isna().all() else round(float(share.mean()), 3),
            "share_last": None if share.isna().all() else round(float(share.iloc[-1]), 3),
            "share_trend": round(_slope(weeks, share.to_numpy(float)), 4) if len(share) else 0.0,
            "xfp_trend": round(_slope(weeks, g["xfp"].to_numpy(float)), 2),
            "snap_pct": None if g["snap_pct"].isna().all() else round(float(g["snap_pct"].mean()), 3),
            "snap_last": None if pd.isna(last.get("snap_pct")) else round(float(last["snap_pct"]), 3),
        })
    return pd.DataFrame(rows)


def forecast(summary, weekly, games, target_week, end_week, cvs=None):
    """
    Adds, per player: this week's forecast with p10/p90, the opponent and
    implied total, and the rest-of-season total and per-game rate through
    end_week (byes count zero).
    """
    if summary.empty:
        return summary
    cvs = cvs or DEFAULT_CV
    sched = team_weeks(games)
    dvp = defense_factors(weekly)
    avg = {w: _avg_implied(sched, w) for w in range(target_week, end_week + 1)}
    out = summary.copy()
    cols = {k: [] for k in ("mean", "p10", "p90", "opponent", "implied", "ros", "ros_pg", "ros_games")}
    for r in out.itertuples():
        unit = max(r.base + r.efficiency, 0.0)
        mult, opp, implied = environment(r.team, r.position, target_week, sched, dvp, avg[target_week])
        mean = unit * mult
        p10, p90 = lognormal_range(mean, cvs.get(r.position, 0.6))
        ros, games_left = 0.0, 0
        for w in range(target_week, end_week + 1):
            m, _, _ = environment(r.team, r.position, w, sched, dvp, avg[w])
            if m > 0:
                games_left += 1
                ros += unit * m
        cols["mean"].append(round(mean, 2))
        cols["p10"].append(round(p10, 1))
        cols["p90"].append(round(p90, 1))
        cols["opponent"].append(opp)
        cols["implied"].append(implied)
        cols["ros"].append(round(ros, 1))
        cols["ros_pg"].append(round(ros / games_left, 2) if games_left else 0.0)
        cols["ros_games"].append(games_left)
    for k, v in cols.items():
        out[k] = v
    return out


def signals(row):
    """Short plain-English flags for one forecast row."""
    flags = []
    if row.get("games", 0) >= 2:
        if row.get("fpoe_pg", 0) <= -3 and row.get("xfp_pg", 0) >= 9:
            flags.append("buy-low: usage well ahead of production")
        if row.get("fpoe_pg", 0) >= 4 and row.get("xfp_pg", 0) < 12:
            flags.append("sell-high: production ahead of usage")
    if row.get("games", 0) >= 3:
        if (row.get("share_trend") or 0) >= 0.03:
            flags.append("role growing")
        elif (row.get("share_trend") or 0) <= -0.03:
            flags.append("role shrinking")
    if row.get("snap_last") is not None and row.get("snap_pct") is not None and row["snap_last"] - row["snap_pct"] <= -0.15:
        flags.append("snaps fell last week")
    return flags


# ------------------------------------------------------------------ lineup and matchup


def win_probability(mine, theirs):
    """P(my total beats theirs) for two lists of (mean, sd), treating players as independent."""
    m = sum(a for a, _ in mine) - sum(a for a, _ in theirs)
    v = sum(s * s for _, s in mine) + sum(s * s for _, s in theirs)
    if v <= 0:
        return 1.0 if m > 0 else 0.0 if m < 0 else 0.5
    return 0.5 * (1 + math.erf(m / math.sqrt(2 * v)))


def sd_for(mean, position, cvs=None):
    """Lognormal standard deviation for a forecast; kickers and D/STs get fixed spreads."""
    if position == "K":
        return 4.0
    if position in ("D/ST", "DST"):
        return 5.5
    cv = (cvs or DEFAULT_CV).get(position, 0.6)
    return max(mean * cv, 1.5) if mean > 0 else 0.0


def best_lineup(players, slot_counts):
    """
    Greedy best lineup. players: dicts with name, position, eligible (slot
    names), value. slot_counts: {slot name: count}. Fills the narrowest slots
    first so a flexible player is not spent on a slot only he could fill.
    """
    order = sorted(slot_counts, key=lambda s: (s.count("/"), s))
    used, lineup = set(), []
    for slot in order:
        for _ in range(int(slot_counts[slot])):
            pool = [p for p in players if p["name"] not in used and slot in (p.get("eligible") or [])]
            if not pool:
                continue
            pick = max(pool, key=lambda p: p["value"])
            used.add(pick["name"])
            lineup.append((slot, pick))
    return lineup
