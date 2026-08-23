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
    ("bye_week", "Bye"),
    ("adp", "ADP"),
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
ASC_KEYS = {"draft_rank", "adp", "bye_week", "name", "position", "pro_team"}

POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "D/ST"]


def columns_for(position):
    """(key, label) pairs for the selected position, identity plus stat cols."""
    extra = POS_STAT_COLS.get(position, [])
    return list(IDENTITY_COLS) + extra


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


def filter_players(df, position="ALL", query=""):
    """Narrow the pool by position and a case-insensitive name/team search."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    out = df
    if position and position != "ALL":
        out = out[out["position"] == position]
    needle = (query or "").strip().lower()
    if needle:
        name = out["name"].fillna("").str.lower()
        team = out["pro_team"].fillna("").str.lower()
        pos = out["position"].fillna("").str.lower()
        out = out[name.str.contains(needle, regex=False)
                  | team.str.contains(needle, regex=False)
                  | pos.str.contains(needle, regex=False)]
    return out


def sort_players(df, key="draft_rank", descending=None):
    """
    Sort by a column. Default direction is ascending for ranks/ADP/bye,
    descending for FPTS and counting stats. Missing values sink to the bottom.
    """
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    if key not in df.columns:
        key = "draft_rank"
    if descending is None:
        descending = key not in ASC_KEYS
    return df.sort_values(
        by=key, ascending=not descending, na_position="last", kind="mergesort"
    ).reset_index(drop=True)


def format_stat(value, kind="num"):
    """Render a projection for the table. None becomes an em dash."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if kind == "text" or isinstance(value, str):
        return value or "—"
    if kind == "int":
        return str(int(round(value)))
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
