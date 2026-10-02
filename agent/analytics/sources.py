"""
Public data the analyst models from, fetched from nflverse and cached on disk.

Every file is a plain CSV on a GitHub release. A cached copy is reused while
it is fresh; a failed download falls back to whatever copy is on disk, so a
GitHub hiccup costs freshness, not the run.
"""
import logging
import time
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE = "https://github.com/nflverse/nflverse-data/releases/download"


def urls(year):
    """name -> (url, max age in hours). Weekly stats change daily in season; the id map rarely."""
    return {
        f"stats_{year}": (f"{BASE}/stats_player/stats_player_week_{year}.csv", 3),
        f"stats_{year - 1}": (f"{BASE}/stats_player/stats_player_week_{year - 1}.csv", 24 * 30),
        f"snaps_{year}": (f"{BASE}/snap_counts/snap_counts_{year}.csv", 3),
        "players": (f"{BASE}/players/players.csv", 24 * 7),
        "games": ("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv", 6),
    }


# Columns kept from each file, so the frames stay small on a 4 GB VPS.
STATS_COLS = [
    "player_id", "player_display_name", "position", "season", "week", "season_type", "team", "opponent_team",
    "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions", "passing_air_yards",
    "carries", "rushing_yards", "rushing_tds", "receptions", "targets", "receiving_yards", "receiving_tds",
    "receiving_air_yards", "target_share", "air_yards_share", "wopr", "fantasy_points_ppr",
]
SNAP_COLS = ["season", "game_type", "week", "pfr_player_id", "team", "offense_snaps", "offense_pct"]
PLAYER_COLS = ["gsis_id", "display_name", "espn_id", "pfr_id", "position", "latest_team"]
GAME_COLS = ["season", "game_type", "week", "gameday", "gametime", "away_team", "home_team",
             "away_score", "home_score", "spread_line", "total_line"]


def _fetch(url, path, max_age_hours):
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    if fresh:
        return True
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and r.content:
            tmp = path.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.replace(path)
            return True
        logger.warning("nflverse %s answered %s", url, r.status_code)
    except requests.RequestException as e:
        logger.warning("nflverse download failed for %s: %s", url, e)
    return path.exists()


def _read(path, cols):
    head = pd.read_csv(path, nrows=0)
    use = [c for c in cols if c in head.columns]
    return pd.read_csv(path, usecols=use, low_memory=False)


def load(cache_dir, year):
    """
    {"stats", "prior", "snaps", "players", "games"} as DataFrames, plus
    "missing": the names that could not be fetched or read. Any frame may be
    empty; the model degrades instead of failing.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out, missing = {}, []
    plan = {
        "stats": (f"stats_{year}", STATS_COLS),
        "prior": (f"stats_{year - 1}", STATS_COLS),
        "snaps": (f"snaps_{year}", SNAP_COLS),
        "players": ("players", PLAYER_COLS),
        "games": ("games", GAME_COLS),
    }
    sources = urls(year)
    for key, (name, cols) in plan.items():
        url, age = sources[name]
        path = cache_dir / f"{name}.csv"
        if not _fetch(url, path, age):
            missing.append(name)
            out[key] = pd.DataFrame(columns=cols)
            continue
        try:
            out[key] = _read(path, cols)
        except Exception as e:
            logger.warning("could not read %s: %s", path, e)
            missing.append(name)
            out[key] = pd.DataFrame(columns=cols)
    if not out["games"].empty:
        out["games"] = out["games"][out["games"]["season"] == year].reset_index(drop=True)
    out["missing"] = missing
    return out
