"""
SQLite-backed storage for weekly fantasy football snapshots.

Kept intentionally simple (stdlib sqlite3, no ORM) since the write volume is
tiny (a handful of rows per team per week) and the main consumer is the Shiny
dashboard doing read-only aggregate queries.
"""
import json
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
    projected_score REAL,
    PRIMARY KEY (year, week, team_id)
);

CREATE TABLE IF NOT EXISTS team_logos (
    year INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    content BLOB NOT NULL,
    content_type TEXT,
    collected_at TEXT,
    PRIMARY KEY (year, team_id)
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

CREATE TABLE IF NOT EXISTS players (
    year INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position TEXT NOT NULL,
    pro_team TEXT,
    bye_week INTEGER,
    injury_status TEXT,
    injured INTEGER,
    percent_owned REAL,
    percent_started REAL,
    adp REAL,
    auction_value REAL,
    draft_rank INTEGER,
    pos_rank INTEGER,
    projected_points REAL,
    projected_avg REAL,
    last_year_points REAL,
    total_points REAL,
    projected_stats TEXT,
    last_year_stats TEXT,
    on_team_id INTEGER,
    collected_at TEXT,
    PRIMARY KEY (year, player_id)
);

CREATE TABLE IF NOT EXISTS trades (
    year INTEGER NOT NULL,
    trade_date INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT,
    position TEXT,
    from_team_id INTEGER,
    from_team_name TEXT,
    to_team_id INTEGER,
    to_team_name TEXT,
    collected_at TEXT,
    PRIMARY KEY (year, trade_date, player_id)
);

CREATE TABLE IF NOT EXISTS draft_picks (
    year INTEGER NOT NULL,
    overall_pick INTEGER NOT NULL,
    round_num INTEGER NOT NULL,
    round_pick INTEGER NOT NULL,
    team_id INTEGER,
    team_name TEXT,
    player_id INTEGER NOT NULL,
    player_name TEXT,
    bid_amount REAL,
    keeper INTEGER,
    collected_at TEXT,
    PRIMARY KEY (year, overall_pick)
);

-- Prose written by the agents for the dashboard: one row per (kind, year,
-- week), replaced when a job runs again. In the database rather than a file
-- so a new piece changes the fingerprint and reaches open tabs on its own.
CREATE TABLE IF NOT EXISTS site_content (
    kind TEXT NOT NULL,
    year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    title TEXT,
    body TEXT NOT NULL,
    run_id TEXT,
    written_at TEXT,
    PRIMARY KEY (kind, year, week)
);

-- Every rostered player in every played week's box score: slot, projection,
-- points. Bench and IR rows included, which is what makes bench regrets,
-- position contribution, and the player leaderboards possible. Replaced per
-- (year, week) on every collect, so a player dropped mid-week does not
-- linger. eligible_slots is a JSON list of slot names ("RB", "RB/WR/TE").
CREATE TABLE IF NOT EXISTS lineup_scores (
    year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT,
    position TEXT,
    pro_team TEXT,
    slot TEXT NOT NULL,
    eligible_slots TEXT,
    projected REAL,
    points REAL,
    injury_status TEXT,
    collected_at TEXT,
    PRIMARY KEY (year, week, team_id, player_id)
);

-- League settings the derivations need and used to guess at: how many
-- teams make the playoffs, how long the regular season is, and the starting
-- slot counts (JSON {slot name: count}) that decide an optimal lineup.
CREATE TABLE IF NOT EXISTS league_settings (
    year INTEGER PRIMARY KEY,
    team_count INTEGER,
    reg_season_count INTEGER,
    playoff_team_count INTEGER,
    slot_counts TEXT,
    collected_at TEXT
);

-- The league activity feed: adds, drops, waiver claims, trades, one row per
-- player moved. date is ESPN's millisecond timestamp. Insert-only, so the
-- ledger keeps moves that have scrolled out of ESPN's recent window.
CREATE TABLE IF NOT EXISTS activity (
    year INTEGER NOT NULL,
    date INTEGER NOT NULL,
    team_id INTEGER,
    team_name TEXT,
    action TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT,
    position TEXT,
    bid_amount REAL,
    collected_at TEXT,
    PRIMARY KEY (year, date, action, player_id)
);

-- Questions asked of the dashboard's data chat, for the daily caps. The
-- viewer key is a per-browser token, not an identity.
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    viewer TEXT,
    question TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER
);
"""

# The kinds of prose the dashboard knows where to show. The agent's write
# tool refuses anything else, so a mistyped kind cannot land as an orphan row.
# recap: last week, on This week. preview: the coming matchups, on Next up.
# power: weekly power rankings, on League.
SITE_CONTENT_KINDS = ("recap", "preview", "power")


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
    "schedule": {
        "projected_score": "REAL",
    },
    "players": {
        # Season points scored so far, from the daily pool collect. The draft
        # return chart reads this; lineup_scores misses points a player
        # scored on another roster or on waivers.
        "total_points": "REAL",
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


def upsert_team_logo(year, team_id, url, content, content_type=None):
    """One team's logo bytes, keyed like the teams row it decorates."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO team_logos
                (year, team_id, url, content, content_type, collected_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (year, team_id, url, content, content_type),
        )


def get_logo_urls():
    """
    (year, team_id) -> source URL for every stored logo. The collector reads
    this before downloading anything, so an unchanged logo costs one row scan
    instead of a re-fetch of every image every morning.
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT year, team_id, url FROM team_logos").fetchall()
        return {(r["year"], r["team_id"]): r["url"] for r in rows}


def get_all_team_logos():
    """Every stored logo, bytes included. A handful of small images per
    season, so loading the lot mirrors how every other table is read."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT year, team_id, url, content, content_type FROM team_logos"
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_schedule(rows):
    """
    rows: iterable of dicts with keys matching the schedule columns.
    """
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO schedule
                (year, week, matchup_period, team_id, opponent_id, is_home,
                 projected_score)
            VALUES
                (:year, :week, :matchup_period, :team_id, :opponent_id, :is_home,
                 :projected_score)
            """,
            [{"projected_score": None, **r} for r in rows],
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


def replace_players(year, rows):
    """
    Replace the player pool for one season.

    Delete-then-insert rather than upsert: a player who drops out of ESPN's
    pool (retired, cut, no longer ranked) would otherwise stay on the draft
    board forever.
    """
    # total_points arrived after the first release; a caller (or a test)
    # building rows without it still writes.
    rows = [{"total_points": None, **r} for r in rows]
    with get_connection() as conn:
        conn.execute("DELETE FROM players WHERE year = ?", (year,))
        conn.executemany(
            """
            INSERT INTO players
                (year, player_id, name, position, pro_team, bye_week,
                 injury_status, injured, percent_owned, percent_started,
                 adp, auction_value, draft_rank, pos_rank, projected_points,
                 projected_avg, last_year_points, total_points, projected_stats,
                 last_year_stats, on_team_id, collected_at)
            VALUES
                (:year, :player_id, :name, :position, :pro_team, :bye_week,
                 :injury_status, :injured, :percent_owned, :percent_started,
                 :adp, :auction_value, :draft_rank, :pos_rank, :projected_points,
                 :projected_avg, :last_year_points, :total_points, :projected_stats,
                 :last_year_stats, :on_team_id, datetime('now'))
            """,
            rows,
        )


def replace_draft_picks(year, rows):
    """Replace one season's draft board. Empty list clears a stale year."""
    with get_connection() as conn:
        conn.execute("DELETE FROM draft_picks WHERE year = ?", (year,))
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO draft_picks
                (year, overall_pick, round_num, round_pick, team_id, team_name,
                 player_id, player_name, bid_amount, keeper, collected_at)
            VALUES
                (:year, :overall_pick, :round_num, :round_pick, :team_id, :team_name,
                 :player_id, :player_name, :bid_amount, :keeper, datetime('now'))
            """,
            rows,
        )


def insert_new_trades(rows):
    """
    Insert trade rows, returning only the ones that were actually new.

    INSERT OR IGNORE row-by-row rather than executemany because the caller
    (the hourly trade check) needs to know which rows it has never seen --
    those are the ones worth announcing to Discord. ESPN's recent-activity
    feed re-serves the same trades every poll, so almost every call inserts
    nothing.
    """
    new = []
    with get_connection() as conn:
        for r in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO trades
                    (year, trade_date, player_id, player_name, position,
                     from_team_id, from_team_name, to_team_id, to_team_name,
                     collected_at)
                VALUES
                    (:year, :trade_date, :player_id, :player_name, :position,
                     :from_team_id, :from_team_name, :to_team_id, :to_team_name,
                     datetime('now'))
                """,
                r,
            )
            if cur.rowcount:
                new.append(r)
    return new


def get_all_trades():
    """Every season's trade rows, one row per player moved, newest trade
    first. Rows sharing (year, trade_date) are one trade."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY year DESC, trade_date DESC, player_name"
        ).fetchall()
        return [dict(r) for r in rows]


def replace_lineup_week(year, week, rows):
    """
    Replace every lineup row for one week. rows: dicts with the lineup_scores
    columns (eligible_slots as a list; stored as JSON). Idempotent, and a
    player who left a roster since the last collect does not linger.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM lineup_scores WHERE year = ? AND week = ?", (int(year), int(week)))
        conn.executemany(
            """
            INSERT OR REPLACE INTO lineup_scores
                (year, week, team_id, player_id, player_name, position, pro_team, slot,
                 eligible_slots, projected, points, injury_status, collected_at)
            VALUES
                (:year, :week, :team_id, :player_id, :player_name, :position, :pro_team, :slot,
                 :eligible_slots, :projected, :points, :injury_status, datetime('now'))
            """,
            [dict(r, eligible_slots=json.dumps(r.get("eligible_slots") or [])) for r in rows],
        )


def get_lineup_weeks(year):
    """Weeks of a season that have lineup rows."""
    with get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT week FROM lineup_scores WHERE year = ?", (int(year),)).fetchall()
        return {int(r["week"]) for r in rows}


def get_all_lineup_scores():
    """Every lineup row, every season, eligible_slots decoded to a list."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM lineup_scores ORDER BY year, week, team_id, slot, player_name"
        ).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            raw = row.get("eligible_slots")
            try:
                row["eligible_slots"] = json.loads(raw) if raw else []
            except (TypeError, ValueError, json.JSONDecodeError):
                row["eligible_slots"] = []
            out.append(row)
        return out


def upsert_league_settings(year, team_count=None, reg_season_count=None, playoff_team_count=None,
                           slot_counts=None):
    """slot_counts: {slot name: count} for the starting slots."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO league_settings
                (year, team_count, reg_season_count, playoff_team_count, slot_counts, collected_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (int(year), team_count, reg_season_count, playoff_team_count,
             json.dumps(slot_counts or {})),
        )


def get_all_league_settings():
    """{year: {team_count, reg_season_count, playoff_team_count, slot_counts}}."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM league_settings").fetchall()
        out = {}
        for r in rows:
            row = dict(r)
            row["slot_counts"] = _loads_json(row.get("slot_counts"))
            out[int(row["year"])] = row
        return out


def insert_new_activity(rows):
    """
    Insert activity rows not already stored (keyed on year, date, action,
    player). Returns the rows that were new.
    """
    new = []
    with get_connection() as conn:
        for r in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO activity
                    (year, date, team_id, team_name, action, player_id, player_name, position,
                     bid_amount, collected_at)
                VALUES
                    (:year, :date, :team_id, :team_name, :action, :player_id, :player_name,
                     :position, :bid_amount, datetime('now'))
                """,
                r,
            )
            if cur.rowcount:
                new.append(r)
    return new


def get_all_activity():
    """Every stored activity row, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM activity ORDER BY year DESC, date DESC, action, player_name"
        ).fetchall()
        return [dict(r) for r in rows]


def log_chat(viewer, question, input_tokens=None, output_tokens=None):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_log (at, viewer, question, input_tokens, output_tokens) VALUES (datetime('now'), ?, ?, ?, ?)",
            (viewer, question, input_tokens, output_tokens),
        )


def chats_today(viewer=None):
    """Questions asked since UTC midnight, for one viewer or everyone."""
    with get_connection() as conn:
        if viewer is None:
            row = conn.execute("SELECT COUNT(*) AS n FROM chat_log WHERE at >= date('now')").fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM chat_log WHERE at >= date('now') AND viewer = ?",
                               (viewer,)).fetchone()
        return int(row["n"])


def get_agent_transactions(limit=50):
    """
    The agent's executed ESPN writes, newest first, or [] when the agent
    service has never created its tables in this database. The dashboard
    shows these as the manager's log; the table itself belongs to agent.store.
    """
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_transactions'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT at, kind, description, reason, ok, dry_run, code, message FROM agent_transactions "
            "ORDER BY at DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_site_content(kind, year, week, body, title=None, run_id=None):
    """
    Write one piece of dashboard prose, replacing the same (kind, year,
    week) if it exists. Raises ValueError for a kind the dashboard has no
    place for.
    """
    if kind not in SITE_CONTENT_KINDS:
        raise ValueError(f"unknown site content kind {kind!r}; known: {', '.join(SITE_CONTENT_KINDS)}")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO site_content
                (kind, year, week, title, body, run_id, written_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (kind, int(year), int(week), title, body, run_id),
        )


def get_all_site_content():
    """Every piece of dashboard prose, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM site_content ORDER BY year DESC, week DESC, kind"
        ).fetchall()
        return [dict(r) for r in rows]


def get_years():
    """Seasons present in scores, the player pool, teams, or the draft."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT year FROM weekly_scores
            UNION
            SELECT year FROM players
            UNION
            SELECT year FROM teams
            UNION
            SELECT year FROM draft_picks
            ORDER BY year DESC
            """
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


def get_all_draft_picks():
    """Every season's draft picks, overall order."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM draft_picks
            ORDER BY year DESC, overall_pick
            """
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


def get_all_players():
    """Every season's player pool, with stats JSON decoded into dicts."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM players
            ORDER BY year DESC, COALESCE(draft_rank, 9999), name
            """
        ).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            row["projected_stats"] = _loads_json(row.get("projected_stats"))
            row["last_year_stats"] = _loads_json(row.get("last_year_stats"))
            out.append(row)
        return out


def _loads_json(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


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

    A week whose rows carry no points at all is not collected either: it is
    a snapshot taken before the week's games were played, and the real
    scores still have to be fetched.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT week FROM weekly_scores WHERE year = ?
            GROUP BY week
            HAVING COUNT(*) = COUNT(matchup_period)
               AND COUNT(*) = COUNT(matchup_score)
               AND COALESCE(SUM(score), 0) + COALESCE(SUM(matchup_score), 0) > 0
            """,
            (year,),
        ).fetchall()
        return {r["week"] for r in rows}


def delete_week(year, week):
    """
    Drops one week's scores and standings snapshot. Returns the number of
    rows removed, so the caller can tell a real purge from a no-op.

    Used when the collector finds a stored week that ESPN says has not been
    played -- rows that can only have come from a pre-kickoff snapshot.
    """
    with get_connection() as conn:
        scores = conn.execute(
            "DELETE FROM weekly_scores WHERE year = ? AND week = ?", (year, week)
        ).rowcount
        standings = conn.execute(
            "DELETE FROM standings_snapshot WHERE year = ? AND week = ?", (year, week)
        ).rowcount
        return scores + standings


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
                   (SELECT COALESCE(SUM(wins), 0) FROM standings_snapshot),
                   (SELECT COUNT(*) FROM players),
                   (SELECT COALESCE(MAX(collected_at), '') FROM players),
                   (SELECT COUNT(*) FROM teams),
                   (SELECT COUNT(*) FROM draft_picks),
                   (SELECT COALESCE(MAX(collected_at), '') FROM draft_picks),
                   (SELECT COUNT(*) FROM schedule),
                   (SELECT COALESCE(SUM(projected_score), 0) FROM schedule),
                   (SELECT COUNT(*) FROM team_logos),
                   (SELECT COALESCE(MAX(collected_at), '') FROM team_logos),
                   (SELECT COUNT(*) FROM trades),
                   (SELECT COUNT(*) FROM site_content),
                   (SELECT COALESCE(MAX(written_at), '') FROM site_content),
                   (SELECT COUNT(*) FROM lineup_scores),
                   (SELECT COALESCE(MAX(collected_at), '') FROM lineup_scores),
                   (SELECT COUNT(*) FROM league_settings),
                   (SELECT COUNT(*) FROM activity)
            """
        ).fetchone())


def last_collected():
    """
    When the newest score or player row was written, as a UTC
    "YYYY-MM-DD HH:MM:SS" string, or None.

    Player rows are included so a preseason collect still drives the
    dashboard's "synced N ago" stamp, when weekly_scores is empty.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT MAX(collected_at) AS collected_at FROM (
                SELECT MAX(collected_at) AS collected_at FROM weekly_scores
                UNION ALL
                SELECT MAX(collected_at) AS collected_at FROM players
                UNION ALL
                SELECT MAX(collected_at) AS collected_at FROM draft_picks
            )
            """
        ).fetchone()
        return row["collected_at"] if row and row["collected_at"] else None
