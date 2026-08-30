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
