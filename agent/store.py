"""
Agent state in the same SQLite file the dashboard reads, in agent_* tables.

Runs, scheduled wakeups, pending asks, the transaction audit log, the
Discord-user-to-team map, and the friends' question quota all live here so
they survive a container restart and so /agent status has one place to look.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from gamedaybot.storage import db

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    job TEXT NOT NULL,
    trigger TEXT,
    params TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    model TEXT,
    num_turns INTEGER,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    searches INTEGER,
    result TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS agent_wakeups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    run_at TEXT NOT NULL,
    params TEXT,
    label TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT,
    run_id TEXT,
    UNIQUE (job, run_at)
);

CREATE TABLE IF NOT EXISTS agent_asks (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    description TEXT NOT NULL,
    reason TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    discord_message_id TEXT,
    run_id TEXT,
    resolution TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    run_id TEXT,
    kind TEXT NOT NULL,
    description TEXT,
    reason TEXT,
    payload TEXT,
    ok INTEGER,
    dry_run INTEGER,
    status INTEGER,
    code TEXT,
    message TEXT,
    transaction_id TEXT,
    outcome TEXT,
    outcome_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_users (
    discord_user_id TEXT PRIMARY KEY,
    team_id INTEGER NOT NULL,
    display_name TEXT,
    claimed_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_ask_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    discord_user_id TEXT,
    team_id INTEGER,
    question TEXT,
    run_id TEXT
);

CREATE TABLE IF NOT EXISTS agent_seen_offers (
    offer_id TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_notes (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init():
    db.init_db()
    with db.get_connection() as conn:
        conn.executescript(SCHEMA)
        _add_missing_columns(conn, "agent_transactions", {"outcome": "TEXT", "outcome_at": "TEXT"})


def _add_missing_columns(conn, table, columns):
    """CREATE TABLE IF NOT EXISTS leaves an existing table alone; this adds what it lacks."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


# ------------------------------------------------------------------- runs

def create_run(job, trigger="schedule", params=None, status="running"):
    run_id = uuid.uuid4().hex[:12]
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO agent_runs (id, job, trigger, params, status, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, job, trigger, json.dumps(params or {}), status, now_iso()),
        )
    return run_id


def start_run(run_id):
    with db.get_connection() as conn:
        conn.execute("UPDATE agent_runs SET status='running', started_at=? WHERE id=?", (now_iso(), run_id))


def finish_run(run_id, status, result=None, error=None, model=None, num_turns=None,
               cost_usd=None, input_tokens=None, output_tokens=None, searches=None):
    with db.get_connection() as conn:
        conn.execute(
            """UPDATE agent_runs SET status=?, finished_at=?, result=?, error=?, model=?, num_turns=?,
               cost_usd=?, input_tokens=?, output_tokens=?, searches=? WHERE id=?""",
            (status, now_iso(), result, error, model, num_turns, cost_usd, input_tokens,
             output_tokens, searches, run_id),
        )


def recent_runs(limit=5):
    with db.get_connection() as conn:
        cur = conn.execute("SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return _rows(cur)


def recent_briefs(limit=6, job=None):
    """
    Finished runs that left a brief, newest first. League members' questions
    (the ask job) are other people's, and the dashboard prose jobs are on the
    site already, so both are left out.
    """
    sql = ("SELECT id, job, started_at, result FROM agent_runs WHERE status='done' AND result IS NOT NULL "
           "AND result != '' AND job NOT IN ('ask', 'recap', 'power', 'preview', 'preview_site')")
    args = []
    if job:
        sql += " AND job=?"
        args.append(job)
    sql += " ORDER BY started_at DESC LIMIT ?"
    args.append(int(limit))
    with db.get_connection() as conn:
        return _rows(conn.execute(sql, args))


def get_run(run_id):
    with db.get_connection() as conn:
        cur = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def running_runs():
    with db.get_connection() as conn:
        return _rows(conn.execute("SELECT * FROM agent_runs WHERE status='running'"))


def mark_stale_runs():
    """Anything still 'running' at startup died with the last process."""
    with db.get_connection() as conn:
        conn.execute("UPDATE agent_runs SET status='aborted', finished_at=? WHERE status='running'", (now_iso(),))


# ---------------------------------------------------------------- wakeups

def add_wakeup(job, run_at, params=None, label=None):
    """Idempotent on (job, run_at): re-planning a week does not duplicate."""
    with db.get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO agent_wakeups (job, run_at, params, label, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (job, run_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
             json.dumps(params or {}), label, now_iso()),
        )


def due_wakeups(now=None):
    now = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with db.get_connection() as conn:
        return _rows(conn.execute(
            "SELECT * FROM agent_wakeups WHERE status='pending' AND run_at <= ? ORDER BY run_at", (now,)))


def pending_wakeups(limit=20):
    with db.get_connection() as conn:
        return _rows(conn.execute(
            "SELECT * FROM agent_wakeups WHERE status='pending' ORDER BY run_at LIMIT ?", (limit,)))


def set_wakeup_status(wakeup_id, status, run_id=None):
    with db.get_connection() as conn:
        conn.execute("UPDATE agent_wakeups SET status=?, run_id=? WHERE id=?", (status, run_id, wakeup_id))


def clear_pending_wakeups(job):
    with db.get_connection() as conn:
        conn.execute("DELETE FROM agent_wakeups WHERE status='pending' AND job=?", (job,))


# ------------------------------------------------------------------- asks

def create_ask(kind, description, payload, reason=None, run_id=None, expiry_hours=24):
    ask_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    with db.get_connection() as conn:
        conn.execute(
            """INSERT INTO agent_asks (id, created_at, expires_at, kind, description, reason, payload, status, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (ask_id, now.isoformat(timespec="seconds"),
             (now + timedelta(hours=expiry_hours)).isoformat(timespec="seconds"),
             kind, description, reason, json.dumps(payload), run_id),
        )
    return ask_id


def get_ask(ask_id):
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM agent_asks WHERE id=?", (ask_id,)).fetchone()
        return dict(row) if row else None


def pending_asks():
    expire_asks()
    with db.get_connection() as conn:
        return _rows(conn.execute("SELECT * FROM agent_asks WHERE status='pending' ORDER BY created_at"))


def unposted_asks():
    with db.get_connection() as conn:
        return _rows(conn.execute(
            "SELECT * FROM agent_asks WHERE status='pending' AND discord_message_id IS NULL ORDER BY created_at"))


def ask_by_message(message_id):
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM agent_asks WHERE discord_message_id=?", (str(message_id),)).fetchone()
        return dict(row) if row else None


def set_ask_message(ask_id, message_id):
    with db.get_connection() as conn:
        conn.execute("UPDATE agent_asks SET discord_message_id=? WHERE id=?", (str(message_id), ask_id))


def resolve_ask(ask_id, status, resolution=None):
    with db.get_connection() as conn:
        conn.execute("UPDATE agent_asks SET status=?, resolution=?, resolved_at=? WHERE id=?",
                     (status, resolution, now_iso(), ask_id))


# Called with each ask that expires, when set (the server points it at the
# ledger). Expiry is the one resolution nobody sees happen.
ask_expired_hook = None


def recent_asks(limit=10):
    with db.get_connection() as conn:
        return _rows(conn.execute("SELECT * FROM agent_asks ORDER BY created_at DESC LIMIT ?", (int(limit),)))


def expire_asks(now=None):
    """Marks overdue pending asks expired and returns them."""
    now = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with db.get_connection() as conn:
        rows = _rows(conn.execute("SELECT * FROM agent_asks WHERE status='pending' AND expires_at < ?", (now,)))
        conn.execute("UPDATE agent_asks SET status='expired', resolved_at=? WHERE status='pending' AND expires_at < ?",
                     (now, now))
    for ask in rows:
        if ask_expired_hook is not None:
            try:
                ask_expired_hook(ask)
            except Exception:
                logger.exception("ask_expired_hook failed for %s", ask.get("id"))
    return rows


# ----------------------------------------------------------- transactions

def log_transaction(kind, payload, result, description=None, reason=None, run_id=None):
    with db.get_connection() as conn:
        conn.execute(
            """INSERT INTO agent_transactions (at, run_id, kind, description, reason, payload, ok, dry_run,
               status, code, message, transaction_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(), run_id, kind, description, reason, json.dumps(payload),
             1 if result.ok else 0, 1 if result.dry_run else 0, result.status, result.code,
             result.message, result.transaction_id),
        )


def recent_transactions(limit=20):
    with db.get_connection() as conn:
        return _rows(conn.execute("SELECT * FROM agent_transactions ORDER BY at DESC LIMIT ?", (limit,)))


def failed_writes_today(kind=None):
    """Non-200 responses since UTC midnight. The rules forbid retrying those today."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    with db.get_connection() as conn:
        if kind:
            cur = conn.execute("SELECT * FROM agent_transactions WHERE ok=0 AND dry_run=0 AND at>=? AND kind=?", (start, kind))
        else:
            cur = conn.execute("SELECT * FROM agent_transactions WHERE ok=0 AND dry_run=0 AND at>=?", (start,))
        return _rows(cur)


def append_to_ask(ask_id, payload, description):
    """
    Chain a fallback onto a pending ask that shares its drop, so one approval
    covers the primary claim and its fallbacks. The payload becomes
    {"chain": [...], "descriptions": [...]} and the description names them.
    """
    ask = get_ask(ask_id)
    if not ask or ask["status"] != "pending":
        return None
    current = json.loads(ask["payload"]) if isinstance(ask["payload"], str) else ask["payload"]
    chain = list(current["chain"]) if "chain" in current else [current]
    descs = list(current.get("descriptions") or [ask["description"]])
    chain.append(payload)
    descs.append(description)
    fallbacks = [d.split(", drop")[0].removeprefix("add ").replace(" via waiver claim", "") for d in descs[1:]]
    full = f"{descs[0]}; fallbacks sharing the drop: {', '.join(fallbacks)}"
    with db.get_connection() as conn:
        conn.execute("UPDATE agent_asks SET payload=?, description=? WHERE id=?",
                     (json.dumps({"chain": chain, "descriptions": descs}), full, ask_id))
    return get_ask(ask_id)


def unresolved_proposals():
    """Trade proposals the agent really sent that have no recorded outcome yet."""
    with db.get_connection() as conn:
        return _rows(conn.execute(
            "SELECT * FROM agent_transactions WHERE kind='trade_propose' AND ok=1 AND dry_run=0 "
            "AND transaction_id IS NOT NULL AND outcome IS NULL ORDER BY at"))


def set_transaction_outcome(row_id, outcome):
    with db.get_connection() as conn:
        conn.execute("UPDATE agent_transactions SET outcome=?, outcome_at=? WHERE id=?", (outcome, now_iso(), row_id))


def agent_proposed_trade_ids():
    with db.get_connection() as conn:
        cur = conn.execute("SELECT transaction_id FROM agent_transactions WHERE kind='trade_propose' AND ok=1 AND transaction_id IS NOT NULL")
        return {r["transaction_id"] for r in cur.fetchall()}


# ------------------------------------------------------------------ users

def claim_team(discord_user_id, team_id, display_name=None):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO agent_users (discord_user_id, team_id, display_name, claimed_at) VALUES (?, ?, ?, ?)",
            (str(discord_user_id), int(team_id), display_name, now_iso()),
        )


def team_for_user(discord_user_id):
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM agent_users WHERE discord_user_id=?", (str(discord_user_id),)).fetchone()
        return dict(row) if row else None


def log_ask(discord_user_id, team_id, question, run_id):
    with db.get_connection() as conn:
        conn.execute("INSERT INTO agent_ask_log (at, discord_user_id, team_id, question, run_id) VALUES (?, ?, ?, ?, ?)",
                     (now_iso(), str(discord_user_id), team_id, question, run_id))


def asks_today(discord_user_id=None):
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    with db.get_connection() as conn:
        if discord_user_id is None:
            row = conn.execute("SELECT COUNT(*) AS n FROM agent_ask_log WHERE at>=?", (start,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM agent_ask_log WHERE at>=? AND discord_user_id=?",
                               (start, str(discord_user_id))).fetchone()
        return int(row["n"])


# ----------------------------------------------------------------- offers

def offer_seen(offer_id):
    with db.get_connection() as conn:
        return conn.execute("SELECT 1 FROM agent_seen_offers WHERE offer_id=?", (str(offer_id),)).fetchone() is not None


def mark_offer_seen(offer_id):
    with db.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO agent_seen_offers (offer_id, seen_at) VALUES (?, ?)", (str(offer_id), now_iso()))


# ------------------------------------------------------------------ notes

def set_note(key, value):
    with db.get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO agent_notes (key, value, updated_at) VALUES (?, ?, ?)",
                     (key, json.dumps(value), now_iso()))


def get_note(key, default=None):
    with db.get_connection() as conn:
        row = conn.execute("SELECT value FROM agent_notes WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default
