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
    collected_at TEXT,
    PRIMARY KEY (year, week, team_id)
);

CREATE TABLE IF NOT EXISTS teams (
    year INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    abbrev TEXT,
    logo_url TEXT,
    owner TEXT,
    PRIMARY KEY (year, team_id)
);

CREATE TABLE IF NOT EXISTS schedule (
    year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    matchup_period INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    opponent_id INTEGER,
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
        # WAL lets the dashboard read while the collector writes. Under the
        # default journal mode the writer takes an exclusive lock, so a read
        # landing mid-snapshot fails with "database is locked". The setting is
        # stored in the file header, so this sticks for every later connection.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _add_missing_columns(conn)


# Columns added after the first release. CREATE TABLE IF NOT EXISTS leaves an
# existing table exactly as it found it, so a database created before a column
# existed needs the ALTER as well as the updated schema above.
_ADDED_COLUMNS = {
    "weekly_scores": {
        "collected_at": "TEXT",
        "matchup_period": "INTEGER",
        "matchup_score": "REAL",
    },
}


def _add_missing_columns(conn):
    for table, columns in _ADDED_COLUMNS.items():
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def upsert_weekly_scores(rows):
    """
    rows: iterable of dicts with keys matching the weekly_scores columns.
    """
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO weekly_scores
                (year, week, team_id, team_name, score, projected_score,
                 opponent_id, opponent_name, is_home, matchup_period,
                 matchup_score, collected_at)
            VALUES
                (:year, :week, :team_id, :team_name, :score, :projected_score,
                 :opponent_id, :opponent_name, :is_home, :matchup_period,
                 :matchup_score, datetime('now'))
            """,
            rows,
        )


def upsert_teams(rows):
    """
    rows: iterable of dicts with keys matching the teams columns.
    """
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO teams
                (year, team_id, team_name, abbrev, logo_url, owner)
            VALUES
                (:year, :team_id, :team_name, :abbrev, :logo_url, :owner)
            """,
            rows,
        )


def upsert_schedule(rows):
    """
    rows: iterable of dicts with keys matching the schedule columns.
    """
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO schedule
                (year, week, matchup_period, team_id, opponent_id, is_home)
            VALUES
                (:year, :week, :matchup_period, :team_id, :opponent_id, :is_home)
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


def get_all_weekly_scores():
    """
    Every season's scores in one query. A full league season is only a few
    hundred rows, so the dashboard loads the lot once per change and filters
    by year in memory rather than re-querying on every interaction.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM weekly_scores ORDER BY year, week, team_name"
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_teams():
    """Every season's team metadata (name, abbrev, logo, owner) in one query."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM teams ORDER BY year, team_name"
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_schedule():
    """Every season's schedule rows in one query."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM schedule ORDER BY year, week, team_id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_latest_standings():
    """The most recent standings snapshot for each season, in one query."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.* FROM standings_snapshot s
            JOIN (
                SELECT year, MAX(week) AS week FROM standings_snapshot GROUP BY year
            ) latest ON s.year = latest.year AND s.week = latest.week
            ORDER BY s.year DESC, s.rank
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_collected_weeks(year):
    """
    Weeks the collector can leave alone, so it can spot gaps.

    A week only counts as collected when every one of its rows carries the
    columns the dashboard needs today -- matchup_period and matchup_score.
    Rows written before those columns existed hold a two-week playoff round's
    total in the per-week `score`, and nothing about them says so, which is
    exactly the case the dashboard renders wrong. Reporting such a week as
    missing costs one extra ESPN call per stale week, once, and lets the
    normal Tuesday run repair a database that would otherwise stay wrong
    forever.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT week FROM weekly_scores WHERE year = ?
            GROUP BY week
            HAVING COUNT(*) = COUNT(matchup_period)
               AND COUNT(*) = COUNT(matchup_score)
            """,
            (year,),
        ).fetchall()
        return {r["week"] for r in rows}


def fingerprint():
    """
    Cheap change-detector driving the dashboard's reactive poll.

    Sums are included alongside counts because snapshots are written with
    INSERT OR REPLACE: a corrected score overwrites a row without changing
    the row count, and a count-only fingerprint would miss it.

    MAX(collected_at) is in here for the case sums still miss: a collection
    that rewrites the same values changes no count and no sum, but the
    dashboard shows how long ago the data was written, and that has moved.

    Polling the file's mtime instead would be unreliable -- WAL writes land
    in the -wal sidecar and leave the main .db file untouched until a
    checkpoint, so mtime can sit still while data changes underneath.
    """
    with get_connection() as conn:
        return tuple(conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM weekly_scores),
                   (SELECT COALESCE(SUM(score), 0) FROM weekly_scores),
                   (SELECT COALESCE(MAX(collected_at), '') FROM weekly_scores),
                   (SELECT COUNT(*) FROM standings_snapshot),
                   (SELECT COALESCE(SUM(wins), 0) FROM standings_snapshot)
            """
        ).fetchone())


def last_collected():
    """
    When the newest score row was written, as a UTC "YYYY-MM-DD HH:MM:SS"
    string, or None.

    Returns None for a database written before collected_at existed, which
    the dashboard treats as "unknown" rather than guessing a time.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(collected_at) AS collected_at FROM weekly_scores"
        ).fetchone()
        return row["collected_at"] if row else None
