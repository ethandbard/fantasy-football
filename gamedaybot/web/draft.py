"""
Draft-board helpers: column sets, filtering, and sorting over the players table.

Pure functions over DataFrames so the cheat-sheet layout in app.py stays
layout-only, and so the rank/FPTS arithmetic can be tested without Shiny.
"""
import pandas as pd

# Shared identity columns, then position-specific counting stats matching
# the ESPN draft board's PC/PA/PY (and the RB/WR/TE/K/DST equivalents).
IDENTITY_COLS = [
    ("draft_rank", "Rk"),
    ("name", "Player"),
    ("position", "Pos"),
    ("pro_team", "Team"),
    ("pick_label", "Pick"),
    ("draft_team", "Club"),
    ("bye_week", "Bye"),
    ("adp", "ADP"),
    ("adp_delta", "+/-"),
    ("percent_owned", "%"),
    ("projected_points", "FPTS"),
    ("last_year_points", "LY"),
]

POS_STAT_COLS = {
    "QB": [
        ("passingCompletions", "PC"),
        ("passingAttempts", "PA"),
        ("passingYards", "PY"),
        ("passingTouchdowns", "PTD"),
        ("passingInterceptions", "INT"),
        ("rushingYards", "RY"),
        ("rushingTouchdowns", "RTD"),
    ],
    "RB": [
        ("rushingAttempts", "ATT"),
        ("rushingYards", "YDS"),
        ("rushingTouchdowns", "TD"),
        ("receivingReceptions", "REC"),
        ("receivingYards", "REY"),
        ("receivingTouchdowns", "RETD"),
        ("receivingTargets", "TAR"),
    ],
    "WR": [
        ("receivingReceptions", "REC"),
        ("receivingYards", "REY"),
        ("receivingTouchdowns", "RETD"),
        ("receivingTargets", "TAR"),
        ("rushingYards", "RY"),
    ],
    "TE": [
        ("receivingReceptions", "REC"),
        ("receivingYards", "REY"),
        ("receivingTouchdowns", "RETD"),
        ("receivingTargets", "TAR"),
    ],
    "K": [
        ("madeFieldGoals", "FG"),
        ("attemptedFieldGoals", "FGA"),
        ("madeExtraPoints", "XP"),
    ],
    "D/ST": [
        ("defensiveSacks", "SACK"),
        ("defensiveInterceptions", "INT"),
        ("defensiveFumbles", "FR"),
        ("defensiveTouchdowns", "TD"),
        ("defensivePointsAllowed", "PA"),
        ("defensiveYardsAllowed", "YA"),
    ],
}

# Columns where a lower number is better, so a first click sorts ascending.
ASC_KEYS = {
    "draft_rank", "adp", "bye_week", "name", "position", "pro_team",
    "pick_label", "draft_team", "overall_pick",
}

COL_WIDTHS = {
    "draft_rank": "40px",
    "name": "minmax(168px, 1.6fr)",
    "position": "44px",
    "pro_team": "48px",
    "pick_label": "48px",
    "draft_team": "minmax(88px, 1fr)",
    "bye_week": "40px",
    "adp": "52px",
    "adp_delta": "44px",
    "percent_owned": "44px",
    "projected_points": "64px",
    "last_year_points": "52px",
}

POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "D/ST"]

# Sentinel club value for "players nobody drafted" -- kept out of the team
# namespace so a club actually named Undrafted could still be filtered.
UNDRAFTED = "__UNDRAFTED__"


def columns_for(position):
    """(key, label) pairs for the selected position, identity plus stat cols."""
    extra = POS_STAT_COLS.get(position, [])
    return list(IDENTITY_COLS) + extra


def column_template(cols):
    """CSS grid-template-columns string for a draft-board column set."""
    return " ".join(COL_WIDTHS.get(key, "52px") for key, _ in cols)


def attach_picks(players_df, picks_df):
    """Join ESPN draft results onto the player pool as draft_team / pick_label."""
    if players_df is None or players_df.empty:
        return players_df if players_df is not None else pd.DataFrame()
    out = players_df.copy()
    if picks_df is None or picks_df.empty:
        out["draft_team"] = None
        out["pick_label"] = None
        out["overall_pick"] = None
        out["adp_delta"] = None
        return out
    slim = picks_df[["player_id", "team_name", "round_num", "round_pick", "overall_pick"]].rename(
        columns={"team_name": "draft_team"}
    )
    out = out.merge(slim, on="player_id", how="left")
    def _label(row):
        if pd.isna(row.get("round_num")) or pd.isna(row.get("round_pick")):
            return None
        return f"{int(row['round_num'])}.{int(row['round_pick'])}"
    out["pick_label"] = out.apply(_label, axis=1)
    # Positive means the pick beat the market: the player fell, taken later
    # than ADP said he'd go. Negative is a reach. Undrafted stays blank.
    if "adp" in out.columns:
        out["adp_delta"] = out["overall_pick"] - out["adp"]
    else:
        out["adp_delta"] = None
    return out


def flatten_stats(df):
    """
    Promote projected_stats JSON keys to columns so the table can sort by PY.

    Identity columns already on the frame win if a name collides.
    """
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    out = df.copy()
    keys = {key for cols in POS_STAT_COLS.values() for key, _ in cols}
    for key in keys:
        if key in out.columns:
            continue
        out[key] = out["projected_stats"].map(
            lambda stats, k=key: (stats or {}).get(k) if isinstance(stats, dict) else None
        )
    return out


def filter_players(df, position="ALL", query="", club="ALL"):
    """Narrow the pool by position, fantasy club, and a name/team search."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    out = df
    if position and position != "ALL":
        out = out[out["position"] == position]
    if club and club != "ALL" and "draft_team" in out.columns:
        if club == UNDRAFTED:
            out = out[out["draft_team"].isna()]
        else:
            out = out[out["draft_team"] == club]
    needle = (query or "").strip().lower()
    if needle:
        name = out["name"].fillna("").str.lower()
        team = out["pro_team"].fillna("").str.lower()
        pos = out["position"].fillna("").str.lower()
        club_col = out["draft_team"].fillna("").str.lower() if "draft_team" in out.columns else ""
        mask = (name.str.contains(needle, regex=False)
                | team.str.contains(needle, regex=False)
                | pos.str.contains(needle, regex=False))
        if isinstance(club_col, pd.Series):
            mask = mask | club_col.str.contains(needle, regex=False)
        out = out[mask]
    return out


def sort_players(df, key="draft_rank", descending=None):
    """
    Sort by a column. Default direction is ascending for ranks/ADP/bye,
    descending for FPTS and counting stats. Missing values sink to the bottom.
    """
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    if key == "pick_label" and "overall_pick" in df.columns:
        # pick_label is a "round.pick" string, which sorts "10.1" before "2.1".
        key = "overall_pick"
    if key not in df.columns:
        key = "draft_rank"
    if descending is None:
        descending = key not in ASC_KEYS
    return df.sort_values(
        by=key, ascending=not descending, na_position="last", kind="mergesort"
    ).reset_index(drop=True)


def steals_and_reaches(board_df, n=3):
    """
    The league's best and worst picks against the market, as two frames.

    A steal went `adp_delta` picks later than ADP said; a reach went earlier.
    Only genuine ones qualify -- a delta of zero is neither -- so either
    frame can come back shorter than n, or empty.
    """
    cols = ["name", "position", "pick_label", "draft_team", "adp_delta"]
    empty = pd.DataFrame(columns=cols)
    if board_df is None or board_df.empty or "adp_delta" not in board_df.columns:
        return empty, empty
    drafted = board_df[board_df["adp_delta"].notna()]
    steals = drafted[drafted["adp_delta"] > 0].nlargest(n, "adp_delta")
    reaches = drafted[drafted["adp_delta"] < 0].nsmallest(n, "adp_delta")
    return steals[cols].reset_index(drop=True), reaches[cols].reset_index(drop=True)


# Roster-shape ordering for the club report cards.
_SHAPE_ORDER = ["QB", "RB", "WR", "TE", "K", "D/ST"]


def club_summaries(board_df):
    """
    One report card per club, ordered by total projected points -- which
    makes the card order itself a projected draft standings.

    Each dict carries the club name, projected total, a roster-shape string
    ("1 QB · 5 RB · ..."), and the club's best value / biggest reach rows
    (None when no pick qualifies).
    """
    if board_df is None or board_df.empty or "draft_team" not in board_df.columns:
        return []
    drafted = board_df[board_df["draft_team"].notna()]
    if drafted.empty:
        return []

    cards = []
    for club, picks in drafted.groupby("draft_team"):
        counts = picks["position"].value_counts()
        shape = " · ".join(
            f"{int(counts[pos])} {pos}" for pos in _SHAPE_ORDER if pos in counts
        )
        valued = picks[picks["adp_delta"].notna()]
        steals = valued[valued["adp_delta"] > 0]
        reaches = valued[valued["adp_delta"] < 0]
        cards.append({
            "club": club,
            "projected": picks["projected_points"].sum(),
            "shape": shape,
            "best_value": (steals.loc[steals["adp_delta"].idxmax()]
                           if not steals.empty else None),
            "biggest_reach": (reaches.loc[reaches["adp_delta"].idxmin()]
                              if not reaches.empty else None),
        })
    cards.sort(key=lambda c: c["projected"], reverse=True)
    return cards


def grid_data(board_df):
    """
    The draft board as drawn on draft day: rounds down, clubs across.

    Clubs are ordered by their round-1 slot, so the columns read in draft
    order and the snake shows up as each even round filling right-to-left.
    Returns (clubs, rows) where rows is [(round_num, [cell-or-None per
    club])] and a cell is the player's row from board_df.
    """
    if (board_df is None or board_df.empty
            or "round_num" not in board_df.columns):
        return [], []
    drafted = board_df[board_df["draft_team"].notna() & board_df["round_num"].notna()]
    if drafted.empty:
        return [], []

    first_round = drafted[drafted["round_num"] == drafted["round_num"].min()]
    clubs = list(first_round.sort_values("round_pick")["draft_team"])
    # A club that traded out of round 1 still needs a column.
    for club in drafted["draft_team"].unique():
        if club not in clubs:
            clubs.append(club)

    rows = []
    for round_num in sorted(drafted["round_num"].unique()):
        in_round = drafted[drafted["round_num"] == round_num].sort_values("round_pick")
        # One cell per club per round; a traded second pick keeps the earlier
        # slot rather than silently replacing it.
        by_club = {}
        for _, row in in_round.iterrows():
            by_club.setdefault(row["draft_team"], row)
        rows.append((int(round_num), [by_club.get(club) for club in clubs]))
    return clubs, rows


def format_stat(value, kind="num"):
    """Render a projection for the table. None becomes an em dash."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if kind == "text" or isinstance(value, str):
        return value or "—"
    if kind == "int":
        return str(int(round(value)))
    if kind == "signed":
        rounded = int(round(value))
        return f"{rounded:+d}" if rounded else "0"
    if kind == "pct":
        return f"{value:.1f}"
    number = float(value)
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.1f}"


def injury_tag(status):
    """One-letter injury chip, or None for healthy/active."""
    if not status or status in ("ACTIVE", "NORMAL"):
        return None
    labels = {
        "QUESTIONABLE": "Q",
        "DOUBTFUL": "D",
        "OUT": "O",
        "INJURY_RESERVE": "IR",
        "SUSPENSION": "SUS",
        "INJURED_RESERVE": "IR",
    }
    return labels.get(status, status[:2])
