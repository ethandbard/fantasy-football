"""
SQLite-backed storage for weekly fantasy football snapshots.

Kept intentionally simple (stdlib sqlite3, no ORM) since the write volume is
tiny (a handful of rows per team per week) and the main consumer is the Shiny
dashboard doing read-only aggregate queries.
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/app/data/fantasy.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_scores (
    year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    score REAL NOT NULL,
    projected_score REAL,
    opponent_id INTEGER,
    opponent_name TEXT,
    is_home INTEGER NOT NULL,
    PRIMARY KEY (year, week, team_id)
);

CREATE TABLE IF NOT EXISTS standings_snapshot (
    year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    ties INTEGER NOT NULL,
    points_for REAL NOT NULL,
    points_against REAL NOT NULL,
    rank INTEGER NOT NULL,
    PRIMARY KEY (year, week, team_id)
);
"""


@contextmanager
def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def upsert_weekly_scores(rows):
    """
    rows: iterable of dicts with keys matching the weekly_scores columns.
    """
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO weekly_scores
                (year, week, team_id, team_name, score, projected_score,
                 opponent_id, opponent_name, is_home)
            VALUES
                (:year, :week, :team_id, :team_name, :score, :projected_score,
                 :opponent_id, :opponent_name, :is_home)
            """,
            rows,
        )


def upsert_standings(rows):
    """
    rows: iterable of dicts with keys matching the standings_snapshot columns.
    """
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO standings_snapshot
                (year, week, team_id, team_name, wins, losses, ties,
                 points_for, points_against, rank)
            VALUES
                (:year, :week, :team_id, :team_name, :wins, :losses, :ties,
                 :points_for, :points_against, :rank)
            """,
            rows,
        )


def get_years():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT year FROM weekly_scores ORDER BY year DESC"
        ).fetchall()
        return [r["year"] for r in rows]


def get_weekly_scores(year):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM weekly_scores WHERE year = ? ORDER BY week, team_name",
            (year,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_standings(year):
    with get_connection() as conn:
        latest_week = conn.execute(
            "SELECT MAX(week) AS w FROM standings_snapshot WHERE year = ?",
            (year,),
        ).fetchone()["w"]
        if latest_week is None:
            return []
        rows = conn.execute(
            """
            SELECT * FROM standings_snapshot
            WHERE year = ? AND week = ?
            ORDER BY rank
            """,
            (year, latest_week),
        ).fetchall()
        return [dict(r) for r in rows]
