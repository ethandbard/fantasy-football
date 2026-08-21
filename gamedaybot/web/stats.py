"""
Season arithmetic for the dashboard.

Pure functions over DataFrames -- no Shiny, no Plotly -- so the numbers can be
tested without standing a server up. Everything here derives from the
weekly_scores table alone, which already carries opponent_id, is_home and
projected_score even though none of the three reached the old UI.

Every function expects a frame already narrowed to a single season, since
week numbers only identify a matchup within one year.
"""
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
    return log.sort_values(["team_name", "week"])


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
    log = game_log(scores_df)
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
    """
    log = game_log(scores_df)
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

        losses = played[played["result"] == "L"]
        if not losses.empty:
            unlucky = losses.loc[losses["score"].idxmax()]
            add("😤", "Highest-Scoring Loss", unlucky,
                f"{unlucky['score']:.1f} pts and still lost — {scoreline(unlucky)}")

        wins = played[played["result"] == "W"]
        if not wins.empty:
            lucky = wins.loc[wins["score"].idxmin()]
            add("🍀", "Lowest-Scoring Win", lucky,
                f"{lucky['score']:.1f} pts and still won — {scoreline(lucky)}")

        records = derive_records(scores_df)
        hot = records.loc[records["streak"].str.startswith("W")]
        if not hot.empty:
            best_run = hot.loc[hot["streak"].str[1:].astype(int).idxmax()]
            awards.append({
                "icon": "📈", "title": "Longest Active Streak",
                "team": best_run["team_name"], "week": None,
                "focus": best_run["team_name"],
                "detail": f"{best_run['streak'][1:]} straight wins",
            })

        if not records.empty:
            beaten = records.loc[records["points_against"].idxmax()]
            awards.append({
                "icon": "🎯", "title": "Most Points Against",
                "team": beaten["team_name"], "week": None,
                "focus": beaten["team_name"],
                "detail": f"{beaten['points_against']} pts faced",
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
    """
    log = game_log(scores_df)
    if log.empty:
        return log.assign(cum_wins=None, cum_points=None, rank=None)

    log = log.sort_values(["team_name", "week"]).copy()
    credit = (log["result"] == "W").astype(float) + 0.5 * (log["result"] == "T")
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
