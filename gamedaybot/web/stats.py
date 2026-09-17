"""
Season arithmetic for the dashboard.

Pure functions over DataFrames -- no Shiny, no Plotly -- so the numbers can be
tested without standing a server up. Everything here derives from the
weekly_scores table alone, which already carries opponent_id, is_home and
projected_score even though none of the three reached the old UI.

Every function expects a frame already narrowed to a single season, since
week numbers only identify a matchup within one year.
"""
import math

import numpy as np
import pandas as pd


def regular_season_weeks(standings_df, scores_df):
    """
    How many of the collected weeks were regular season.

    Derived rather than configured: a team's wins + losses + ties is exactly
    the number of games it has played, and ESPN's standings only count the
    regular season. So the boundary falls out of data we already store, and a
    league with a 13- or 15-week season needs no setting changed.

    Falls back to every collected week when standings are missing, which is
    the honest answer for a season backfilled scores-first.
    """
    if scores_df.empty:
        return 0

    last_week = int(scores_df["week"].max())
    if standings_df.empty:
        return last_week

    played = standings_df[["wins", "losses", "ties"]].sum(axis=1).max()
    return min(int(played), last_week) if played else last_week


def game_log(scores_df):
    """
    Every team-week with its opponent's score, margin and result attached.

    A self-join on (week, opponent_id) rather than a lookup per row: the old
    dashboard walked the whole list of rows to find each opponent, which is
    quadratic and was recomputed on every render. This is the one derivation
    the rest of the module builds on.
    """
    if scores_df.empty:
        return scores_df.assign(opponent_score=None, margin=None, result=None)

    opponent = (scores_df[["week", "team_id", "score"]]
                .rename(columns={"team_id": "opponent_id", "score": "opponent_score"}))

    log = scores_df.merge(opponent, on=["week", "opponent_id"], how="left")
    log["margin"] = log["score"] - log["opponent_score"]
    log["result"] = np.select(
        [log["margin"] > 0, log["margin"] < 0],
        ["W", "L"],
        # A margin that is exactly zero is a tie; a margin that is missing
        # means the opponent's row was never collected, which is not one.
        default=np.where(log["margin"].isna(), "", "T"),
    )
    # A playoff round spans two weeks under one matchup_period; older rows
    # collected before that column existed fall back to one round per week.
    if "matchup_period" in log.columns:
        log["matchup_period"] = log["matchup_period"].fillna(log["week"])
    else:
        log["matchup_period"] = log["week"]
    return log.sort_values(["team_name", "week"])


def matchup_log(scores_df):
    """
    One row per (team, matchup_period) instead of per week, so a two-week
    playoff round counts as a single game.

    Uses matchup_score -- the round total ESPN actually decided the win on --
    rather than summing the per-week `score` column, since the per-week
    figures come from a lineup split (or an even split) that is only an
    estimate of how the round's total broke down.
    """
    if scores_df.empty:
        return scores_df.assign(
            opponent_score=None, margin=None, result=None, matchup_period=None
        )

    df = scores_df.copy()
    if "matchup_period" in df.columns:
        df["matchup_period"] = df["matchup_period"].fillna(df["week"])
    else:
        df["matchup_period"] = df["week"]
    if "matchup_score" in df.columns:
        df["matchup_score"] = df["matchup_score"].fillna(df["score"])
    else:
        df["matchup_score"] = df["score"]

    rounds = (df.groupby(["team_id", "team_name", "matchup_period"], sort=False)
                .agg(week=("week", "max"),
                     score=("matchup_score", "first"),
                     opponent_id=("opponent_id", "first"),
                     opponent_name=("opponent_name", "first"),
                     is_home=("is_home", "first"))
                .reset_index())

    opponent = (rounds[["matchup_period", "team_id", "score"]]
                .rename(columns={"team_id": "opponent_id", "score": "opponent_score"}))
    log = rounds.merge(opponent, on=["matchup_period", "opponent_id"], how="left")
    log["margin"] = log["score"] - log["opponent_score"]
    log["result"] = np.select(
        [log["margin"] > 0, log["margin"] < 0],
        ["W", "L"],
        default=np.where(log["margin"].isna(), "", "T"),
    )
    return log.sort_values(["team_name", "matchup_period"])


def _streak(results):
    """Trailing run of the same result, as 'W3' / 'L2' / '' for no games."""
    played = [r for r in results if r]
    if not played:
        return ""
    last = played[-1]
    run = 0
    for r in reversed(played):
        if r != last:
            break
        run += 1
    return f"{last}{run}"


def derive_records(scores_df, last_n=5):
    """
    A record per team over whatever weeks were handed in.

    Returns one row per team with: wins/losses/ties and a formatted record,
    win percentage, points for and against, point differential, the current
    streak, and the last `last_n` results as a string.

    Ordered by wins then points for -- a rule that can be stated in one line,
    which is the point. ESPN's own seed uses division and head-to-head
    tiebreaks it does not publish, so the two orders can disagree; the
    dashboard shows both rather than pretending either is the whole story.
    """
    log = matchup_log(scores_df)
    if log.empty:
        return pd.DataFrame(columns=[
            "team_id", "team_name", "wins", "losses", "ties", "record",
            "win_pct", "points_for", "points_against", "diff", "streak", "form",
        ])

    rows = []
    for (team_id, team_name), games in log.groupby(["team_id", "team_name"], sort=False):
        games = games.sort_values("week")
        results = games["result"].tolist()
        wins = results.count("W")
        losses = results.count("L")
        ties = results.count("T")
        played = wins + losses + ties

        rows.append({
            "team_id": team_id,
            "team_name": team_name,
            "wins": wins,
            "losses": losses,
            "ties": ties,
            # Ties were a column of zeros eating its own width; folded into
            # the record and only shown when a tie actually happened.
            "record": f"{wins}-{losses}-{ties}" if ties else f"{wins}-{losses}",
            "win_pct": round((wins + 0.5 * ties) / played, 3) if played else 0.0,
            # Whole points at season-total scale. A tenth of a point decides
            # a single game, so weekly scores keep their decimal -- but a
            # 1800-point season total carrying one is spurious precision, and
            # it is what left a column reading 1814 next to 1795.7 with the
            # digits refusing to line up.
            "points_for": int(round(games["score"].sum())),
            "points_against": int(round(games["opponent_score"].sum())),
            "diff": int(round(games["margin"].sum())),
            "streak": _streak(results),
            "form": " ".join(r for r in results[-last_n:] if r),
        })

    out = pd.DataFrame(rows)
    return (out.sort_values(["wins", "points_for"], ascending=[False, False])
               .reset_index(drop=True))


def consistency(scores_df):
    """
    Who is steady and who is a coin flip.

    The Spread tab used to show eight box plots and leave the reader to
    eyeball which was widest. Standard deviation answers it directly, and the
    coefficient of variation makes a high-scoring volatile team comparable to
    a low-scoring steady one.
    """
    if scores_df.empty:
        return pd.DataFrame(columns=["team_name", "median", "std", "cv", "floor", "ceiling"])

    grouped = scores_df.groupby("team_name")["score"]
    out = pd.DataFrame({
        "median": grouped.median().round(1),
        "std": grouped.std().round(1),
        "floor": grouped.min().round(1),
        "ceiling": grouped.max().round(1),
    })
    mean = grouped.mean()
    out["cv"] = (grouped.std() / mean * 100).round(1)
    return out.reset_index().sort_values("std")


def head_to_head(scores_df):
    """
    Every pairing's record, as a team x opponent table.

    Returns (records, margins): records holds "2-0" style strings, margins
    holds the average points difference, which is what the grid is tinted by.
    Uses the matchup-level view so a two-week playoff round counts once.
    """
    log = matchup_log(scores_df)
    teams = sorted(log["team_name"].unique()) if not log.empty else []
    records = pd.DataFrame("", index=teams, columns=teams, dtype=object)
    margins = pd.DataFrame(np.nan, index=teams, columns=teams, dtype=float)

    if log.empty:
        return records, margins

    for (team, opponent), games in log.groupby(["team_name", "opponent_name"]):
        if team not in teams or opponent not in teams:
            continue
        results = games["result"].tolist()
        wins, losses = results.count("W"), results.count("L")
        records.at[team, opponent] = f"{wins}-{losses}"
        margins.at[team, opponent] = round(games["margin"].mean(), 1)

    return records, margins


def _cross_season(scores_df):
    """
    A multi-year frame with `week` and `matchup_period` offset by year, so
    the matchup-level views never merge two different seasons' week 3 (or
    round 2) into one game just because the numbers match. `year` and the
    original week survive as `season_week` for display.

    A no-op on a single-season frame beyond adding `season_week`.
    """
    if scores_df.empty or "year" not in scores_df.columns:
        return scores_df.assign(season_week=scores_df.get("week"))

    df = scores_df.copy()
    offset = df["year"].astype(int) * 1000
    df["season_week"] = df["week"]
    df["week"] = offset + df["week"]
    mp = df["matchup_period"] if "matchup_period" in df.columns else df["season_week"]
    mp = mp.fillna(df["season_week"])
    df["matchup_period"] = offset + mp.astype(int)
    return df


def head_to_head_all_time(scores_df):
    """head_to_head() over every season in scores_df at once, via _cross_season
    so identical round numbers in different years don't collide."""
    return head_to_head(_cross_season(scores_df))


def _longest_streak(scores_df, result_char):
    """
    Longest run of `result_char` ('W' or 'L') by any team, anywhere in the
    scoped range -- not just the trailing run `_streak()` reports. Uses the
    round-level view so a two-week playoff round counts as one result.

    Returns (team_name, length, start_week, end_week), or None if no team
    has a run of that result.
    """
    log = matchup_log(scores_df)
    if log.empty:
        return None

    best = None
    for (_, team_name), games in log.groupby(["team_id", "team_name"], sort=False):
        games = games.sort_values("week")
        run_len = cur_len = 0
        run_start = run_end = cur_start = None
        for _, g in games.iterrows():
            if g["result"] == result_char:
                if cur_len == 0:
                    cur_start = g["week"]
                cur_len += 1
                if cur_len > run_len:
                    run_len, run_start, run_end = cur_len, cur_start, g["week"]
            else:
                cur_len = 0
        if run_len and (best is None or run_len > best[1]):
            best = (team_name, run_len, int(run_start), int(run_end))
    return best


def trophies(scores_df):
    """
    The season's highlights.

    Ten awards where the old table had four, all read off columns already in
    the database -- projected_score in particular was collected from day one
    and had never been shown. Each entry carries the value with its unit and,
    for the matchup awards, a real scoreline, so "0.8" is never left sitting
    under a column called Detail.
    """
    log = game_log(scores_df)
    if log.empty:
        return []

    # Highest-Scoring Loss / Lowest-Scoring Win read off the round's actual
    # W/L, not a single week's -- a team can put up its best week of the
    # round and still lose the round on the other week's number.
    round_result = matchup_log(scores_df)[["team_id", "matchup_period", "result"]] \
        .rename(columns={"result": "round_result"})
    log = log.merge(round_result, on=["team_id", "matchup_period"], how="left")

    played = log[log["result"] != ""]
    awards = []

    def add(icon, title, row, detail, team=None):
        awards.append({
            "icon": icon,
            "title": title,
            "team": team if team is not None else row["team_name"],
            "week": int(row["week"]),
            "detail": detail,
            # What clicking the card should single out. The matchup awards
            # put "A vs B" in `team` for display, which is not a team name
            # anything else can filter on.
            "focus": row["team_name"],
        })

    def scoreline(row):
        return (f"{row['team_name']} {row['score']:.1f} – "
                f"{row['opponent_score']:.1f} {row['opponent_name']}")

    best = log.loc[log["score"].idxmax()]
    add("🔥", "Highest Score", best, f"{best['score']:.1f} pts")

    worst = log.loc[log["score"].idxmin()]
    add("🥶", "Lowest Score", worst, f"{worst['score']:.1f} pts")

    if not played.empty:
        # One row per matchup rather than two: every game appears once from
        # each side, so the home row alone is the whole matchup.
        matchups = played[played["is_home"] == 1]
        if not matchups.empty:
            closest = matchups.loc[matchups["margin"].abs().idxmin()]
            add("😅", "Closest Matchup", closest,
                f"{abs(closest['margin']):.1f} pt margin — {scoreline(closest)}",
                team=f"{closest['team_name']} vs {closest['opponent_name']}")

            blowout = matchups.loc[matchups["margin"].abs().idxmax()]
            add("💥", "Biggest Blowout", blowout,
                f"{abs(blowout['margin']):.1f} pt margin — {scoreline(blowout)}",
                team=f"{blowout['team_name']} vs {blowout['opponent_name']}")

        losses = played[played["round_result"] == "L"]
        if not losses.empty:
            unlucky = losses.loc[losses["score"].idxmax()]
            add("😤", "Highest-Scoring Loss", unlucky,
                f"{unlucky['score']:.1f} pts and still lost — {scoreline(unlucky)}")

        wins = played[played["round_result"] == "W"]
        if not wins.empty:
            lucky = wins.loc[wins["score"].idxmin()]
            add("🍀", "Lowest-Scoring Win", lucky,
                f"{lucky['score']:.1f} pts and still won — {scoreline(lucky)}")

        records = derive_records(scores_df)

        win_streak = _longest_streak(scores_df, "W")
        if win_streak:
            team_name, length, start_week, end_week = win_streak
            week_text = (f"weeks {start_week}–{end_week}"
                         if start_week != end_week else f"week {start_week}")
            awards.append({
                "icon": "📈", "title": "Longest Win Streak",
                "team": team_name, "week": None,
                "focus": team_name,
                "detail": f"{length} straight wins ({week_text})",
            })

        loss_streak = _longest_streak(scores_df, "L")
        if loss_streak:
            team_name, length, start_week, end_week = loss_streak
            week_text = (f"weeks {start_week}–{end_week}"
                         if start_week != end_week else f"week {start_week}")
            awards.append({
                "icon": "📉", "title": "Longest Losing Streak",
                "team": team_name, "week": None,
                "focus": team_name,
                "detail": f"{length} straight losses ({week_text})",
            })

        if not records.empty:
            beaten = records.loc[records["points_against"].idxmax()]
            awards.append({
                "icon": "🎯", "title": "Most Points Against",
                "team": beaten["team_name"], "week": None,
                "focus": beaten["team_name"],
                "detail": f"{beaten['points_against']} pts faced",
            })

            top_scorer = records.loc[records["points_for"].idxmax()]
            awards.append({
                "icon": "🏆", "title": "Most Points For",
                "team": top_scorer["team_name"], "week": None,
                "focus": top_scorer["team_name"],
                "detail": f"{top_scorer['points_for']} pts",
            })

    projected = log[log["projected_score"].notna() & (log["projected_score"] > 0)].copy()
    if not projected.empty:
        projected["vs_proj"] = projected["score"] - projected["projected_score"]
        over = projected.loc[projected["vs_proj"].idxmax()]
        add("🚀", "Best vs Projection", over,
            f"{over['vs_proj']:+.1f} over a {over['projected_score']:.1f} projection")

        under = projected.loc[projected["vs_proj"].idxmin()]
        add("🧊", "Worst vs Projection", under,
            f"{under['vs_proj']:+.1f} under a {under['projected_score']:.1f} projection")

    swings = log.sort_values(["team_name", "week"]).copy()
    swings["swing"] = swings.groupby("team_name")["score"].diff()
    if swings["swing"].notna().any():
        biggest = swings.loc[swings["swing"].abs().idxmax()]
        add("🎢", "Biggest Week-to-Week Swing", biggest,
            f"{biggest['swing']:+.1f} pts from week {int(biggest['week']) - 1}")

    return awards


def split_detail(detail):
    """
    A trophy's detail split into its number and the unit that follows it.

    The record book stacks points, margins, counts, records and projection
    deltas in one column, where a bare "0.8" under a bare "185.4" says
    nothing about which is a margin and which is a score. This lives beside
    trophies() because that is what writes the strings being taken apart --
    the two have to agree on the phrasing, so they belong in one file.

    Returns (number, unit); the unit is empty when the detail is a bare value
    like a win-loss record, and is dropped down to one word when what follows
    the number is a sentence rather than a unit.
    """
    head = detail.split(" — ")[0].split(" (")[0].strip()
    words = head.split()
    if not words:
        return detail, ""

    number, rest = words[0], words[1:]
    # "over a 131.0 projection" is a phrase, not a unit -- and the projection
    # itself is already spelled out on the row's scoreline.
    if rest[:1] and rest[0] in ("over", "under"):
        return number, "vs proj"
    return number, " ".join(rest) if len(rest) <= 2 else rest[0]


def rank_by_week(scores_df):
    """
    Where every team stood after each week.

    Recomputed from the game log rather than read out of standings_snapshot,
    because that table only holds a usable history for seasons collected live
    -- collect_historical_season writes a single final-week snapshot, so a
    backfilled season would have no curve to draw at all. Deriving it here
    means the chart works for every season in the database.

    Ranked on cumulative wins then cumulative points for, the same stateable
    rule the standings table uses.

    A playoff round's win is only credited on the round's last week, so a
    two-week round does not show a phantom win at its midpoint -- the
    per-week x-positions are unchanged, only when the win lands on them.
    """
    log = game_log(scores_df)
    if log.empty:
        return log.assign(cum_wins=None, cum_points=None, rank=None)

    log = log.sort_values(["team_name", "week"]).copy()

    round_result = matchup_log(scores_df)[["team_id", "matchup_period", "result"]] \
        .rename(columns={"result": "round_result"})
    log = log.merge(round_result, on=["team_id", "matchup_period"], how="left")

    last_week_of_round = log.groupby("matchup_period")["week"].transform("max")
    is_final_week = log["week"] == last_week_of_round
    credited_result = log["round_result"].where(is_final_week, "")
    credit = (credited_result == "W").astype(float) + 0.5 * (credited_result == "T")
    log["cum_wins"] = credit.groupby(log["team_name"]).cumsum()
    log["cum_points"] = log.groupby("team_name")["score"].cumsum()

    ordered = log.sort_values(
        ["week", "cum_wins", "cum_points"], ascending=[True, False, False]
    ).copy()
    ordered["rank"] = ordered.groupby("week").cumcount() + 1
    return ordered


def cumulative_points(scores_df):
    """
    Running points-for and points-against totals after each week.

    Built on the game log so points against comes out of the same self-join
    every other derivation uses. An opponent row that was never collected
    counts as zero, matching how derive_records sums the season.
    """
    log = game_log(scores_df)
    if log.empty:
        return log.assign(cum_pf=None, cum_pa=None)
    log = log.sort_values(["team_name", "week"]).copy()
    log["cum_pf"] = log.groupby("team_name")["score"].cumsum()
    log["cum_pa"] = log["opponent_score"].fillna(0.0).groupby(log["team_name"]).cumsum()
    return log


def vs_projection(scores_df):
    """
    Average points over or under ESPN's projection, per team.

    projected_score has been collected since the first snapshot and never
    shown anywhere. Rows without a projection are dropped rather than counted
    as zero, so an early season that predates projections does not read as
    every team underperforming.
    """
    if scores_df.empty or "projected_score" not in scores_df:
        return pd.DataFrame(columns=["team_name", "vs_proj", "weeks"])

    df = scores_df[scores_df["projected_score"].notna() &
                   (scores_df["projected_score"] > 0)].copy()
    if df.empty:
        return pd.DataFrame(columns=["team_name", "vs_proj", "weeks"])

    df["vs_proj"] = df["score"] - df["projected_score"]
    out = df.groupby("team_name").agg(
        vs_proj=("vs_proj", "mean"), weeks=("vs_proj", "size")
    ).reset_index()
    out["vs_proj"] = out["vs_proj"].round(1)
    return out.sort_values("vs_proj", ascending=False)


def all_time_trophies(scores_df):
    """
    League records across every season in the database at once.

    Mirrors trophies(), but scans the whole history rather than one season,
    and keys every award on (year, week) via _cross_season so two seasons'
    week 3 never collide into a single fake matchup. Championships are
    skipped -- that needs playoff-bracket/final-round logic this schema
    doesn't carry yet.
    """
    if scores_df.empty:
        return []

    cross = _cross_season(scores_df)
    log = game_log(cross)
    if log.empty:
        return []
    played = log[log["result"] != ""]
    awards = []

    def add(icon, title, row, detail, team=None):
        awards.append({
            "icon": icon,
            "title": title,
            "team": team if team is not None else row["team_name"],
            "year": int(row["year"]),
            "week": int(row["season_week"]),
            "detail": detail,
            "focus": row["team_name"],
        })

    def scoreline(row):
        return (f"{row['team_name']} {row['score']:.1f} – "
                f"{row['opponent_score']:.1f} {row['opponent_name']}")

    best = log.loc[log["score"].idxmax()]
    add("🔥", "Highest Single-Week Score", best,
        f"{best['score']:.1f} pts ({int(best['year'])})")

    worst = log.loc[log["score"].idxmin()]
    add("🥶", "Lowest Single-Week Score", worst,
        f"{worst['score']:.1f} pts ({int(worst['year'])})")

    if not played.empty:
        matchups = played[played["is_home"] == 1].copy()
        decisive = matchups[matchups["margin"] != 0]
        if not decisive.empty:
            tight = decisive.loc[decisive["margin"].abs().idxmin()]
            add("😅", "Smallest Margin of Victory", tight,
                f"{abs(tight['margin']):.1f} pts — {scoreline(tight)} ({int(tight['year'])})",
                team=f"{tight['team_name']} vs {tight['opponent_name']}")

            blowout = decisive.loc[decisive["margin"].abs().idxmax()]
            add("💥", "Largest Margin of Victory", blowout,
                f"{abs(blowout['margin']):.1f} pts — {scoreline(blowout)} ({int(blowout['year'])})",
                team=f"{blowout['team_name']} vs {blowout['opponent_name']}")

        if not matchups.empty:
            matchups["combined"] = matchups["score"] + matchups["opponent_score"]
            highest = matchups.loc[matchups["combined"].idxmax()]
            add("🎆", "Highest-Scoring Matchup", highest,
                f"{highest['combined']:.1f} combined pts — "
                f"{scoreline(highest)} ({int(highest['year'])})",
                team=f"{highest['team_name']} vs {highest['opponent_name']}")

    season_totals = (cross.groupby(["year", "team_id", "team_name"])["score"]
                      .sum().reset_index())
    if not season_totals.empty:
        top = season_totals.loc[season_totals["score"].idxmax()]
        awards.append({
            "icon": "🏆", "title": "Most Points in a Season",
            "team": top["team_name"], "year": int(top["year"]), "week": None,
            "focus": top["team_name"],
            "detail": f"{top['score']:.1f} pts ({int(top['year'])})",
        })

    season_records = []
    for yr, yr_scores in scores_df.groupby("year"):
        rec = derive_records(yr_scores)
        if rec.empty:
            continue
        rec = rec.assign(year=yr)
        season_records.append(rec)
    if season_records:
        all_records = pd.concat(season_records, ignore_index=True)
        best_rec = all_records.loc[all_records["win_pct"].idxmax()]
        awards.append({
            "icon": "👑", "title": "Best Season Record",
            "team": best_rec["team_name"], "year": int(best_rec["year"]), "week": None,
            "focus": best_rec["team_name"],
            "detail": f"{best_rec['record']} ({int(best_rec['year'])})",
        })
        worst_rec = all_records.loc[all_records["win_pct"].idxmin()]
        awards.append({
            "icon": "🪦", "title": "Worst Season Record",
            "team": worst_rec["team_name"], "year": int(worst_rec["year"]), "week": None,
            "focus": worst_rec["team_name"],
            "detail": f"{worst_rec['record']} ({int(worst_rec['year'])})",
        })

    # Streaks scanned one season at a time -- a run must not be allowed to
    # bridge two different years just because the week numbers are adjacent
    # once offset.
    best_streak = worst_streak = None
    for yr, yr_scores in scores_df.groupby("year"):
        win_run = _longest_streak(yr_scores, "W")
        if win_run and (best_streak is None or win_run[1] > best_streak[2]):
            best_streak = (yr, *win_run)
        loss_run = _longest_streak(yr_scores, "L")
        if loss_run and (worst_streak is None or loss_run[1] > worst_streak[2]):
            worst_streak = (yr, *loss_run)

    if best_streak:
        yr, team_name, length, start_week, end_week = best_streak
        week_text = (f"weeks {start_week}–{end_week}"
                     if start_week != end_week else f"week {start_week}")
        awards.append({
            "icon": "📈", "title": "Longest Win Streak", "team": team_name,
            "year": int(yr), "week": None, "focus": team_name,
            "detail": f"{length} straight wins ({week_text}, {int(yr)})",
        })
    if worst_streak:
        yr, team_name, length, start_week, end_week = worst_streak
        week_text = (f"weeks {start_week}–{end_week}"
                     if start_week != end_week else f"week {start_week}")
        awards.append({
            "icon": "📉", "title": "Longest Losing Streak", "team": team_name,
            "year": int(yr), "week": None, "focus": team_name,
            "detail": f"{length} straight losses ({week_text}, {int(yr)})",
        })

    projected = log[log["projected_score"].notna() & (log["projected_score"] > 0)].copy()
    if not projected.empty:
        projected["vs_proj"] = projected["score"] - projected["projected_score"]
        over = projected.loc[projected["vs_proj"].idxmax()]
        add("🚀", "Best Week vs Projection", over,
            f"{over['vs_proj']:+.1f} over a {over['projected_score']:.1f} "
            f"projection ({int(over['year'])})")

        under = projected.loc[projected["vs_proj"].idxmin()]
        add("🧊", "Worst Week vs Projection", under,
            f"{under['vs_proj']:+.1f} under a {under['projected_score']:.1f} "
            f"projection ({int(under['year'])})")

    records_tbl, _margins = head_to_head_all_time(scores_df)
    best_pair = None
    for team_name in records_tbl.index:
        for opp in records_tbl.columns:
            rec = records_tbl.at[team_name, opp]
            if not rec:
                continue
            wins, losses = (int(x) for x in rec.split("-"))
            games = wins + losses
            if games < 2:
                continue
            pct = wins / games
            if best_pair is None or (pct, games) > (best_pair[0], best_pair[3]):
                best_pair = (pct, team_name, opp, games, rec)
    if best_pair:
        _, team_name, opp, _games, rec = best_pair
        awards.append({
            "icon": "😈", "title": "Most Dominant Rivalry",
            "team": f"{team_name} vs {opp}", "year": None, "week": None,
            "focus": team_name,
            "detail": f"{rec} all-time against {opp}",
        })

    return awards


def upcoming_week(schedule_df, scores_df):
    """
    The next scheduled week that has no collected scores: the first schedule
    week past the latest collected one, or the schedule's first week when
    nothing has been collected (the preseason case). None when the schedule
    is empty or the season has been played out.

    Both frames must already be narrowed to one season.
    """
    if schedule_df is None or schedule_df.empty:
        return None
    latest = 0 if scores_df is None or scores_df.empty else int(scores_df["week"].max())
    remaining = schedule_df[schedule_df["week"] > latest]
    return int(remaining["week"].min()) if not remaining.empty else None


def win_probability(scores_a, scores_b):
    """
    P(team A outscores team B), from each side's scored weeks treated as a
    normal distribution -- a deliberately rough model, and labelled as such
    where it renders. The difference of two normals is normal, so the answer
    is one CDF evaluation. None until both teams have at least three scored
    weeks, since a standard deviation from fewer is noise.
    """
    a = pd.Series(scores_a).dropna()
    b = pd.Series(scores_b).dropna()
    if len(a) < 3 or len(b) < 3:
        return None
    mean = a.mean() - b.mean()
    spread = float(np.sqrt(a.var() + b.var()))
    if spread == 0:
        return 1.0 if mean > 0 else (0.0 if mean < 0 else 0.5)
    z = mean / spread
    return float(0.5 * (1 + math.erf(z / math.sqrt(2))))


# ------------------------------------------------------------ luck and odds

def all_play(scores_df):
    """
    Every team's record had it played everyone every week.

    A real schedule hands each team one opponent a week, so a 130-point week
    against the one team that scored 135 is worth exactly as much as a
    60-point week against the same opponent: nothing. All-play strips the
    schedule out. Each week a team is credited with a win over every team it
    outscored, so all_play_wins / (teams - 1) is the share of possible
    opponents it would have beaten, and that share summed over the weeks is
    the number of wins its scoring "deserved" (expected_wins). luck is the
    gap between the wins the schedule actually delivered and that number.

    Actual wins come from the round-level view so a two-week playoff round
    counts once; all-play is judged week by week, since that is the grain
    the scores arrive at. A week where two teams tie exactly is worth half
    an all-play win in the expectation and appears in neither count column.
    Sorted luckiest first.
    """
    cols = ["team_id", "team_name", "wins", "losses", "all_play_wins",
            "all_play_losses", "expected_wins", "luck", "points_for",
            "points_against"]
    if scores_df is None or scores_df.empty:
        return pd.DataFrame(columns=cols)

    records = derive_records(scores_df).set_index("team_id")
    tally = {tid: [0, 0, 0.0] for tid in records.index}

    for _, wk in game_log(scores_df).groupby("week"):
        field = len(wk)
        if field < 2:
            continue
        week_scores = wk["score"].to_numpy(dtype=float)
        for tid, own in zip(wk["team_id"], week_scores):
            if tid not in tally or np.isnan(own):
                continue
            beat = int((week_scores < own).sum())
            lost = int((week_scores > own).sum())
            tied = field - 1 - beat - lost
            tally[tid][0] += beat
            tally[tid][1] += lost
            tally[tid][2] += (beat + 0.5 * tied) / (field - 1)

    rows = []
    for tid, rec in records.iterrows():
        ap_wins, ap_losses, expected = tally[tid]
        rows.append({
            "team_id": tid,
            "team_name": rec["team_name"],
            "wins": int(rec["wins"]),
            "losses": int(rec["losses"]),
            "all_play_wins": ap_wins,
            "all_play_losses": ap_losses,
            "expected_wins": round(expected, 2),
            "luck": round(rec["wins"] + 0.5 * rec["ties"] - expected, 2),
            "points_for": rec["points_for"],
            "points_against": rec["points_against"],
        })
    return (pd.DataFrame(rows, columns=cols)
              .sort_values(["luck", "wins"], ascending=[False, False])
              .reset_index(drop=True))


def _with_projection(scores_df):
    """Rows that carry a real projection, with actual - projected attached.
    A missing or zero projection is the collector not having one, not ESPN
    projecting a shutout, so those weeks are dropped rather than counted."""
    if scores_df is None or scores_df.empty or "projected_score" not in scores_df:
        base = list(scores_df.columns) if scores_df is not None else []
        return pd.DataFrame(columns=base + ["delta"])
    df = scores_df[scores_df["projected_score"].notna() &
                   (scores_df["projected_score"] > 0)].copy()
    df["delta"] = df["score"] - df["projected_score"]
    return df


def projection_accuracy(scores_df):
    """
    How far each team lands from ESPN's projection, per team.

    mean_delta keeps the sign (a team that always beats its projection reads
    positive), mae drops it (how far off the projection tends to be either
    way), and beat_rate is the share of weeks over the line -- the number a
    manager will actually quote. Sorted by mean_delta, best first.
    """
    cols = ["team_id", "team_name", "games", "mean_delta", "mae", "beat_rate"]
    df = _with_projection(scores_df)
    if df.empty:
        return pd.DataFrame(columns=cols)

    out = (df.groupby(["team_id", "team_name"], sort=False)["delta"]
             .agg(games="size",
                  mean_delta="mean",
                  mae=lambda s: s.abs().mean(),
                  beat_rate=lambda s: (s > 0).mean())
             .reset_index())
    out["mean_delta"] = out["mean_delta"].round(1)
    out["mae"] = out["mae"].round(1)
    out["beat_rate"] = out["beat_rate"].round(3)
    return out.sort_values("mean_delta", ascending=False).reset_index(drop=True)


def projection_by_week(scores_df):
    """
    The league's projection miss per week: mean actual - projected and the
    share of teams that beat their number. A week where share_over is well
    off 0.5 is a week ESPN misread the whole slate, not one team.
    """
    cols = ["week", "league_mean_delta", "share_over"]
    df = _with_projection(scores_df)
    if df.empty:
        return pd.DataFrame(columns=cols)

    out = (df.groupby("week")["delta"]
             .agg(league_mean_delta="mean", share_over=lambda s: (s > 0).mean())
             .reset_index())
    out["league_mean_delta"] = out["league_mean_delta"].round(1)
    out["share_over"] = out["share_over"].round(3)
    return out.sort_values("week").reset_index(drop=True)


# Fewer games than this and a team's own mean and spread are pulled toward
# the league's, since two weeks say more about variance than about the team.
_SHRINK_GAMES = 4
# Weekly scores swing by more than this even for the steadiest team; a
# smaller sample sd is a fluke of the sample, not a property of the roster.
_SD_FLOOR = 12.0


def _remaining_games(schedule_df, last_played, reg_weeks, known):
    """
    Each unplayed regular-season matchup once, as (team_id, opponent_id).

    Home rows only, because every game appears from both sides; a schedule
    stored without a home flag falls back to de-duplicating the pair per
    week. Games involving a team with no scores yet are dropped, since there
    is nothing to model that team's score on.
    """
    if schedule_df is None or schedule_df.empty or last_played >= reg_weeks:
        return []

    rem = schedule_df[(schedule_df["week"] > last_played) &
                      (schedule_df["week"] <= reg_weeks)]
    rem = rem[rem["team_id"].isin(known) & rem["opponent_id"].isin(known)]
    if rem.empty:
        return []

    if "is_home" in rem.columns and (rem["is_home"] == 1).any():
        rem = rem[rem["is_home"] == 1]
    else:
        pair = rem.apply(
            lambda r: (r["week"], min(r["team_id"], r["opponent_id"]),
                       max(r["team_id"], r["opponent_id"])), axis=1)
        rem = rem[~pair.duplicated()]
    return [(int(t), int(o)) for t, o in zip(rem["team_id"], rem["opponent_id"])]


def playoff_odds(scores_df, schedule_df, reg_weeks, playoff_teams,
                 sims=4000, seed=0):
    """
    Monte Carlo playoff odds over the rest of the regular season.

    Each remaining game is settled by drawing both teams' scores from a
    normal fitted to their played weeks, then the season is ranked on wins
    and points for -- the same one-line rule derive_records uses, and
    deliberately not ESPN's unpublished division and head-to-head tiebreaks.
    A team's sd is floored at _SD_FLOOR, and with fewer than _SHRINK_GAMES
    games both its mean and sd are blended toward the league's in proportion
    to how many games are missing. A tie in a simulated game is half a win
    each.

    Deterministic under `seed`. Once the regular season is over, or nothing
    remains on the schedule, there is nothing to draw and the odds are 1/0
    from the actual standings. wins_now counts a tie as half a win.

    Returns per team: team_id, team_name, wins_now, avg_wins, playoff_odds
    (0-1), top_seed_odds, games_left, sorted by playoff_odds then avg_wins.
    """
    cols = ["team_id", "team_name", "wins_now", "avg_wins", "playoff_odds",
            "top_seed_odds", "games_left"]
    if scores_df is None or scores_df.empty or not reg_weeks:
        return pd.DataFrame(columns=cols)

    reg = scores_df[scores_df["week"] <= reg_weeks]
    if reg.empty:
        return pd.DataFrame(columns=cols)
    last_played = int(reg["week"].max())

    records = derive_records(reg)
    team_ids = [int(t) for t in records["team_id"]]
    index = {tid: i for i, tid in enumerate(team_ids)}
    n = len(team_ids)
    wins_now = (records["wins"] + 0.5 * records["ties"]).to_numpy(dtype=float)
    pf_now = records["points_for"].to_numpy(dtype=float)

    games = _remaining_games(schedule_df, last_played, reg_weeks, index)
    games_left = np.zeros(n, dtype=int)
    for home, away in games:
        games_left[index[home]] += 1
        games_left[index[away]] += 1

    # Score model per team.
    league_mean = float(reg["score"].mean())
    league_sd = float(reg["score"].std()) if len(reg) > 1 else _SD_FLOOR
    if np.isnan(league_sd):
        league_sd = _SD_FLOOR
    means = np.full(n, league_mean)
    sds = np.full(n, max(league_sd, _SD_FLOOR))
    per_team = reg.groupby("team_id")["score"].agg(["mean", "std", "count"])
    for tid, row in per_team.iterrows():
        i = index[int(tid)]
        k = int(row["count"])
        mean = float(row["mean"])
        sd = float(row["std"]) if k > 1 and not np.isnan(row["std"]) else league_sd
        if k < _SHRINK_GAMES:
            weight = k / _SHRINK_GAMES
            mean = weight * mean + (1 - weight) * league_mean
            sd = weight * sd + (1 - weight) * league_sd
        means[i] = mean
        sds[i] = max(sd, _SD_FLOOR)

    sims = max(int(sims), 1) if games else 1
    rng = np.random.default_rng(seed)
    wins = np.tile(wins_now, (sims, 1))
    pf = np.tile(pf_now, (sims, 1))
    if games:
        home = np.array([index[h] for h, _ in games])
        away = np.array([index[a] for _, a in games])
        home_pts = rng.normal(means[home], sds[home], size=(sims, len(games)))
        away_pts = rng.normal(means[away], sds[away], size=(sims, len(games)))
        home_win = home_pts > away_pts
        tie = home_pts == away_pts
        for g in range(len(games)):
            wins[:, home[g]] += home_win[:, g] + 0.5 * tie[:, g]
            wins[:, away[g]] += (~home_win[:, g] & ~tie[:, g]) + 0.5 * tie[:, g]
            pf[:, home[g]] += home_pts[:, g]
            pf[:, away[g]] += away_pts[:, g]

    # Wins dominate points in the standings rule; 1e6 clears any season total.
    key = wins * 1e6 + pf
    order = np.argsort(-key, axis=1, kind="stable")
    rank = np.empty_like(order)
    rank[np.arange(sims)[:, None], order] = np.arange(n)

    out = pd.DataFrame({
        "team_id": team_ids,
        "team_name": records["team_name"].tolist(),
        "wins_now": wins_now,
        "avg_wins": wins.mean(axis=0).round(2),
        "playoff_odds": (rank < playoff_teams).mean(axis=0).round(3),
        "top_seed_odds": (rank == 0).mean(axis=0).round(3),
        "games_left": games_left,
    }, columns=cols)
    return (out.sort_values(["playoff_odds", "avg_wins"], ascending=[False, False])
               .reset_index(drop=True))


# ------------------------------------------------------------------ lineups

# Slots a player can sit in without scoring for the team.
BENCH_SLOTS = ("BE", "IR")
# The positions a lineup breaks down into; D/ST and K are positions in
# ESPN's data even though they are not people.
POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K"]
# ESPN flex slots that are not spelled as a "/"-joined list of positions.
_FLEX_ALIASES = {"OP", "DP", "FLEX"}


def _is_flex_slot(slot, slot_counts):
    """
    A flex slot is one that borrows from dedicated slots: "RB/WR/TE" is a
    flex because RB is a starting slot of its own, while "D/ST" is a
    dedicated slot that merely has a slash in its name.
    """
    if slot in _FLEX_ALIASES:
        return True
    parts = slot.split("/")
    return len(parts) > 1 and any(p in slot_counts for p in parts)


def _points(value):
    """A points cell as a float, with None/NaN read as zero."""
    if value is None:
        return 0.0
    value = float(value)
    return 0.0 if math.isnan(value) else value


def optimal_lineup(rows, slot_counts):
    """
    The best lineup a team could have started, in hindsight.

    `rows` are dicts with player_name, position, slot, eligible_slots and
    points; `slot_counts` maps starting slot name to how many of it the
    league starts. Returns (optimal_points, chosen) with chosen a list of
    (slot, player_name, points).

    Greedy: dedicated slots are filled first, each with its highest-scoring
    eligible players, and flex slots last from whoever is left. That is
    optimal because of how ESPN defines eligibility -- a flex slot accepts
    exactly the players the dedicated slots it spans accept, and every player
    has one position, so no player is ever a candidate for two dedicated
    slots. Filling a dedicated slot with its best player therefore never
    costs a better arrangement elsewhere, and what the flex gets is the best
    of the rest. A lineup rule that broke that (two overlapping flex types,
    or dual-position players) would need a proper assignment.

    IR players are skipped, since ESPN will not let them into a slot, and so
    are rows with no eligibility list -- there is nothing to place them by.
    """
    pool = []
    for r in rows:
        if r.get("slot") == "IR":
            continue
        eligible = r.get("eligible_slots") or []
        if isinstance(eligible, str) or len(eligible) == 0:
            continue
        pool.append((_points(r.get("points")), r.get("player_name"), set(eligible)))
    # Stable sort keeps the caller's order among equal scores.
    pool.sort(key=lambda p: -p[0])

    starting = [(slot, int(count)) for slot, count in slot_counts.items()
                if slot not in BENCH_SLOTS and count]
    dedicated = [sc for sc in starting if not _is_flex_slot(sc[0], slot_counts)]
    flex = [sc for sc in starting if _is_flex_slot(sc[0], slot_counts)]

    chosen, used = [], set()
    for slot, count in dedicated + flex:
        taken = 0
        for i, (pts, name, eligible) in enumerate(pool):
            if taken >= count:
                break
            if i in used or slot not in eligible:
                continue
            used.add(i)
            chosen.append((slot, name, pts))
            taken += 1
    return round(sum(p for _, _, p in chosen), 2), chosen


def _game_context(scores_df):
    """{(year, week, team_id): (team_name, opponent_score, result)} from the
    game log, season by season so week numbers never cross years."""
    context = {}
    if scores_df is None or scores_df.empty:
        return context
    seasons = (scores_df.groupby("year") if "year" in scores_df.columns
               else [(None, scores_df)])
    for yr, season in seasons:
        for _, g in game_log(season).iterrows():
            key = (None if yr is None else int(yr), int(g["week"]), int(g["team_id"]))
            context[key] = (g["team_name"], g["opponent_score"], g["result"])
    return context


def bench_regrets(lineup_df, slot_counts, scores_df):
    """
    Per team-week, the points left on the bench and whether they mattered.

    actual_points sums what the starters scored, optimal_points is
    optimal_lineup() over the whole roster, and regret is the difference.
    opponent_score and the W/L/T result come from the game log for that
    (year, week, team), and flipped is True when the optimal lineup would
    have turned a loss into a win -- the only regret anyone remembers.
    """
    cols = ["year", "week", "team_id", "team_name", "actual_points",
            "optimal_points", "regret", "opponent_score", "result", "flipped"]
    if lineup_df is None or lineup_df.empty or not slot_counts:
        return pd.DataFrame(columns=cols)

    context = _game_context(scores_df)
    names = {}
    if scores_df is not None and not scores_df.empty:
        names = dict(zip(scores_df["team_id"].astype(int), scores_df["team_name"]))

    rows = []
    for (yr, wk, tid), roster in lineup_df.groupby(["year", "week", "team_id"]):
        yr, wk, tid = int(yr), int(wk), int(tid)
        players = roster.to_dict("records")
        starters = [p for p in players if p.get("slot") not in BENCH_SLOTS]
        actual = round(sum(_points(p.get("points")) for p in starters), 2)
        optimal, _ = optimal_lineup(players, slot_counts)

        name, opp, result = context.get(
            (yr, wk, tid), context.get((None, wk, tid),
                                       (names.get(tid, f"Team {tid}"), np.nan, "")))
        opp = np.nan if opp is None else opp
        rows.append({
            "year": yr, "week": wk, "team_id": tid, "team_name": name,
            "actual_points": actual,
            "optimal_points": optimal,
            "regret": round(optimal - actual, 2),
            "opponent_score": opp,
            "result": result,
            "flipped": bool(result == "L" and pd.notna(opp) and optimal > opp),
        })
    return (pd.DataFrame(rows, columns=cols)
              .sort_values(["year", "week", "team_id"])
              .reset_index(drop=True))


def bench_regret_summary(regrets_df):
    """Season totals of bench_regrets() per team, worst offender first."""
    cols = ["team_id", "team_name", "total_regret", "avg_regret", "weeks",
            "flipped_losses"]
    if regrets_df is None or regrets_df.empty:
        return pd.DataFrame(columns=cols)

    out = (regrets_df.sort_values(["year", "week"])
                     .groupby("team_id")
                     .agg(team_name=("team_name", "last"),
                          total_regret=("regret", "sum"),
                          avg_regret=("regret", "mean"),
                          weeks=("regret", "size"),
                          flipped_losses=("flipped", "sum"))
                     .reset_index())
    out["total_regret"] = out["total_regret"].round(1)
    out["avg_regret"] = out["avg_regret"].round(1)
    out["flipped_losses"] = out["flipped_losses"].astype(int)
    return (out[cols].sort_values("total_regret", ascending=False)
                     .reset_index(drop=True))


def position_contribution(lineup_df, names=None):
    """
    Where each team's starting points came from, by position.

    A flex starter counts under the player's own position, not the slot --
    the question is "how much did your running backs score", not "what did
    the flex slot yield". `total` is every starter's points, so shares over
    the six listed positions sum to 1 unless the league starts something
    else (IDP, say). `names` ({team_id: team_name}) attaches a team_name
    column, since lineup rows only carry the id.
    """
    shares = [f"share_{p}" for p in POSITIONS]
    cols = ["team_id"] + POSITIONS + ["total"] + shares
    if names:
        cols = cols + ["team_name"]
    if lineup_df is None or lineup_df.empty:
        return pd.DataFrame(columns=cols)

    starters = lineup_df[~lineup_df["slot"].isin(BENCH_SLOTS)].copy()
    if starters.empty:
        return pd.DataFrame(columns=cols)
    starters["points"] = starters["points"].fillna(0.0).astype(float)

    wide = starters.pivot_table(index="team_id", columns="position",
                                values="points", aggfunc="sum", fill_value=0.0)
    for p in POSITIONS:
        if p not in wide.columns:
            wide[p] = 0.0
    out = wide[POSITIONS].round(1)
    out["total"] = starters.groupby("team_id")["points"].sum().round(1)
    for p in POSITIONS:
        out[f"share_{p}"] = (out[p] / out["total"]).where(out["total"] > 0, 0.0).round(3)
    out = out.reset_index()
    out.columns.name = None
    if names:
        out["team_name"] = out["team_id"].map(lambda t: names.get(int(t), f"Team {t}"))
    return out[cols].sort_values("total", ascending=False).reset_index(drop=True)


def position_contribution_long(contrib_df):
    """position_contribution() melted to (team_id, position, points, share),
    which is the shape a stacked bar wants."""
    cols = ["team_id", "position", "points", "share"]
    if contrib_df is None or contrib_df.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for _, r in contrib_df.iterrows():
        for p in POSITIONS:
            rows.append({"team_id": r["team_id"], "position": p,
                         "points": r[p], "share": r[f"share_{p}"]})
    return pd.DataFrame(rows, columns=cols)


def player_leaderboard(lineup_df, min_weeks=1):
    """
    Season totals per player across every roster that carried them.

    points_total counts every rostered week, bench included; the *_as_starter
    columns count only the weeks a manager actually played them, which is
    what a leaderboard of "who won you games" wants. boom_rate and bust_rate
    are the share of starts at or above 1.5x and at or below 0.5x the
    player's own starting average -- a player's volatility relative to
    himself, so a kicker and a WR1 are judged on the same scale. team_id is
    the most recent roster. team_name is left to the caller, since lineup
    rows only carry the id.

    Sorted by points_as_starter, then points_total.
    """
    cols = ["player_id", "player_name", "position", "team_id", "weeks_rostered",
            "starts", "points_total", "points_as_starter", "avg_as_starter",
            "best_week", "best_points", "boom_rate", "bust_rate"]
    if lineup_df is None or lineup_df.empty:
        return pd.DataFrame(columns=cols)

    order = ["year", "week"] if "year" in lineup_df.columns else ["week"]
    df = lineup_df.sort_values(order).copy()
    df["points"] = df["points"].fillna(0.0).astype(float)
    df["is_starter"] = ~df["slot"].isin(BENCH_SLOTS)

    rows = []
    for pid, g in df.groupby("player_id", sort=False):
        last = g.iloc[-1]
        starts = g[g["is_starter"]]
        n_starts = len(starts)
        as_starter = float(starts["points"].sum())
        avg = as_starter / n_starts if n_starts else 0.0
        best = g.loc[g["points"].idxmax()]
        if n_starts and avg > 0:
            boom = float((starts["points"] >= 1.5 * avg).mean())
            bust = float((starts["points"] <= 0.5 * avg).mean())
        else:
            boom = bust = 0.0
        rows.append({
            "player_id": pid,
            "player_name": last["player_name"],
            "position": last["position"],
            "team_id": int(last["team_id"]),
            "weeks_rostered": len(g),
            "starts": n_starts,
            "points_total": round(float(g["points"].sum()), 1),
            "points_as_starter": round(as_starter, 1),
            "avg_as_starter": round(avg, 1),
            "best_week": int(best["week"]),
            "best_points": round(float(best["points"]), 1),
            "boom_rate": round(boom, 3),
            "bust_rate": round(bust, 3),
        })

    out = pd.DataFrame(rows, columns=cols)
    out = out[out["weeks_rostered"] >= min_weeks]
    return (out.sort_values(["points_as_starter", "points_total"],
                            ascending=[False, False])
               .reset_index(drop=True))


# -------------------------------------------------------------------- draft

def draft_return(picks_df, players_df, window=8, tag_count=5, bust_rounds=6):
    """
    What each draft pick returned against what its slot usually returns.

    total_points comes from the players table (season points wherever the
    player scored them, which lineup rows would miss), zero when missing.
    `expected` is a centred rolling median of total_points over `window`
    picks in draft order -- a median because one league-winning pick 12
    should not raise the bar for picks 8 through 16, and a rolling one
    because the draft's value curve has no reason to follow a formula. delta
    is the return over that bar.

    tag is "steal" for the `tag_count` biggest deltas and "bust" for the
    `tag_count` smallest among rounds 1..`bust_rounds`, where a miss actually
    hurt; a late-round zero is the expected outcome, not a bust. Keepers are
    excluded from both, since their price was set a year earlier.
    """
    cols = ["overall_pick", "round_num", "team_id", "team_name", "player_id",
            "player_name", "position", "total_points", "expected", "delta",
            "tag", "keeper"]
    if picks_df is None or picks_df.empty:
        return pd.DataFrame(columns=cols)

    picks = picks_df.sort_values("overall_pick").copy()
    picks = picks.drop(columns=[c for c in ("position", "total_points")
                                if c in picks.columns])
    if "keeper" not in picks.columns:
        picks["keeper"] = False
    picks["keeper"] = picks["keeper"].fillna(0).astype(bool)

    if players_df is not None and not players_df.empty:
        keys = ["player_id"] + (["year"] if "year" in picks.columns
                                and "year" in players_df.columns else [])
        info = players_df.copy()
        if "position" not in info.columns:
            info["position"] = None
        if "total_points" not in info.columns:
            info["total_points"] = np.nan
        info = info[keys + ["position", "total_points"]].drop_duplicates(keys)
        picks = picks.merge(info, on=keys, how="left")
    else:
        picks["position"] = None
        picks["total_points"] = np.nan

    picks["total_points"] = picks["total_points"].fillna(0.0).astype(float).round(1)
    picks["expected"] = (picks["total_points"]
                         .rolling(window, center=True, min_periods=1)
                         .median().round(1))
    picks["delta"] = (picks["total_points"] - picks["expected"]).round(1)

    picks["tag"] = ""
    eligible = picks[~picks["keeper"]]
    steals = eligible.nlargest(tag_count, "delta").index
    busts = (eligible[(eligible["round_num"] <= bust_rounds) &
                      ~eligible.index.isin(steals)]
             .nsmallest(tag_count, "delta").index)
    picks.loc[steals, "tag"] = "steal"
    picks.loc[busts, "tag"] = "bust"

    return picks.reindex(columns=cols).reset_index(drop=True)


# ------------------------------------------------------------------ playoffs

def _playoff_round_count(playoff_team_count):
    """Rounds a single-elimination bracket of this size takes: 2 -> 1,
    4 -> 2, 6 and 8 -> 3. A six-team field gives the top two a bye."""
    if not playoff_team_count or playoff_team_count < 2:
        return 1
    return int(math.ceil(math.log2(playoff_team_count)))


def bracket(scores_df, playoff_team_count, reg_weeks):
    """
    The playoff rounds after `reg_weeks`, one entry per round.

    Each round is {"round", "weeks", "games"} with a game per matchup
    (home side first) carrying the names, ids, seeds, scores, the winner
    (None for a tie) and a `kind`:

      "playoff"      an earlier-round game between two seeds still alive
      "final"        the last round's game between the two teams that won
                     every earlier playoff game
      "third"        the last round's game between the two teams that each
                     lost exactly once, in the semifinal
      "consolation"  everything else, including the losers' ladder

    Seeds are the top `playoff_team_count` teams by derive_records over the
    regular season, and the last round is the one a bracket of that size
    needs, so a half-collected postseason reports its rounds as "playoff"
    until the final actually arrives. Rounds come from matchup_log, so a
    two-week round is one game.
    """
    if scores_df is None or scores_df.empty or not reg_weeks:
        return []

    regular = scores_df[scores_df["week"] <= reg_weeks]
    seeds = [int(t) for t in derive_records(regular)["team_id"].head(playoff_team_count)]
    seed_of = {tid: i + 1 for i, tid in enumerate(seeds)}

    post = scores_df[scores_df["week"] > reg_weeks].copy()
    if post.empty:
        return []
    if "matchup_period" in post.columns:
        post["matchup_period"] = post["matchup_period"].fillna(post["week"])
    else:
        post["matchup_period"] = post["week"]

    log = matchup_log(post)
    total_rounds = _playoff_round_count(playoff_team_count)
    alive = set(seeds)
    eliminated_in = {}
    rounds = []

    for n, period in enumerate(sorted(log["matchup_period"].unique()), start=1):
        this_round = log[(log["matchup_period"] == period) & log["opponent_score"].notna()]
        if "is_home" in this_round.columns and (this_round["is_home"] == 1).any():
            this_round = this_round[this_round["is_home"] == 1]
        elif not this_round.empty:
            pair = this_round.apply(
                lambda r: (min(r["team_id"], r["opponent_id"]),
                           max(r["team_id"], r["opponent_id"])), axis=1)
            this_round = this_round[~pair.duplicated()]

        games, losers = [], []
        for _, g in this_round.iterrows():
            home, away = int(g["team_id"]), int(g["opponent_id"])
            home_score, away_score = float(g["score"]), float(g["opponent_score"])
            if home_score > away_score:
                winner, loser = g["team_name"], away
            elif away_score > home_score:
                winner, loser = g["opponent_name"], home
            else:
                winner, loser = None, None

            seeded = home in seed_of and away in seed_of
            both_alive = home in alive and away in alive
            if n == total_rounds and seeded and both_alive:
                kind = "final"
            elif (n == total_rounds and seeded
                  and eliminated_in.get(home) == n - 1
                  and eliminated_in.get(away) == n - 1):
                kind = "third"
            elif n < total_rounds and seeded and both_alive:
                kind = "playoff"
            else:
                kind = "consolation"

            games.append({
                "home": g["team_name"], "away": g["opponent_name"],
                "home_id": home, "away_id": away,
                "home_seed": seed_of.get(home), "away_seed": seed_of.get(away),
                "home_score": home_score, "away_score": away_score,
                "winner": winner, "kind": kind,
            })
            if loser is not None:
                losers.append(loser)

        # Eliminations land after the round, so both games of a round are
        # judged on who was alive going in.
        for loser in losers:
            if loser in alive:
                alive.discard(loser)
                eliminated_in[loser] = n

        weeks = sorted(int(w) for w in
                       post.loc[post["matchup_period"] == period, "week"].unique())
        rounds.append({"round": n, "weeks": weeks, "games": games})

    return rounds


def champion(scores_df, playoff_team_count, reg_weeks):
    """
    The final's winner, or None while the season is still going.

    "Still going" is judged two ways, since the scores table has no flag for
    it: the bracket has not reached the round a field of this size needs,
    or the last round has been collected for fewer weeks than an earlier
    round ran -- a two-week final with one week in. A two-week final after
    one-week semifinals slips past that check and reads as decided a week
    early; the collector's next pass corrects it.
    """
    rounds = bracket(scores_df, playoff_team_count, reg_weeks)
    total = _playoff_round_count(playoff_team_count)
    if len(rounds) < total:
        return None
    last = rounds[total - 1]
    earlier = [len(r["weeks"]) for r in rounds[:total - 1]]
    if earlier and len(last["weeks"]) < max(earlier):
        return None
    for game in last["games"]:
        if game["kind"] == "final":
            return game["winner"]
    return None


def champions(all_scores_df, settings_by_year):
    """
    {year: champion} across every season with the settings to decide one.

    `settings_by_year` is {year: {"playoff_team_count", "reg_season_count"}};
    a year without both is skipped rather than guessed at, and so is a year
    whose bracket has no decided final yet.
    """
    out = {}
    if (all_scores_df is None or all_scores_df.empty
            or "year" not in all_scores_df.columns):
        return out
    for yr, season in all_scores_df.groupby("year"):
        yr = int(yr)
        settings = settings_by_year.get(yr) or settings_by_year.get(str(yr)) or {}
        teams = settings.get("playoff_team_count")
        reg = settings.get("reg_season_count")
        if not teams or not reg:
            continue
        winner = champion(season, int(teams), int(reg))
        if winner:
            out[yr] = winner
    return out
