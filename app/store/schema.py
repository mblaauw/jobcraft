"""SQLite schema operations."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime

from .. import config
from .connection import connect
from .jobs import normalize_posted_at

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    agent TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    opencode_message_id TEXT,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    model TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, opencode_message_id)
);

CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    kind TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    path TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Phase 2/3 (created now so later phases need no migration)
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT,
    kind TEXT,
    config_json TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    last_scan_at TEXT,
    last_status TEXT
);

CREATE TABLE IF NOT EXISTS token_df (
    token TEXT PRIMARY KEY,
    n INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    url TEXT UNIQUE,
    title TEXT,
    company TEXT,
    location TEXT,
    hours TEXT,
    description TEXT,
    posted_at TEXT,
    first_seen TEXT,
    last_seen TEXT,
    content_hash TEXT,
    search_text TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    match_score REAL,
    match_reason TEXT,
    profile_hash TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL,
    docx_path TEXT,
    pdf_path TEXT,
    meta_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, kind, version)
);
"""



def init_db() -> None:
    _backup_before_upgrade()
    with connect() as con:
        con.executescript(SCHEMA)
        session_columns = {row["name"] for row in con.execute("PRAGMA table_info(sessions)")}
        if "context_digest" not in session_columns:
            con.execute("ALTER TABLE sessions ADD COLUMN context_digest TEXT")
        columns = {row["name"] for row in con.execute("PRAGMA table_info(messages)")}
        if "hidden" not in columns:
            con.execute("ALTER TABLE messages ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        job_columns = {row["name"] for row in con.execute("PRAGMA table_info(jobs)")}
        for column, ddl in (
            ("lat", "REAL"),
            ("lon", "REAL"),
            ("vote", "INTEGER NOT NULL DEFAULT 0"),
            ("rank_score", "REAL"),
            ("typed_signals", "TEXT"),
            ("signals_key", "TEXT"),
            ("signal_confidence", "REAL"),
            ("search_text", "TEXT"),
        ):
            if column not in job_columns:
                con.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")
        usage_columns = {row["name"] for row in con.execute("PRAGMA table_info(usage_log)")}
        if "agent" not in usage_columns:
            con.execute("ALTER TABLE usage_log ADD COLUMN agent TEXT")
        con.execute(
            "CREATE TABLE IF NOT EXISTS feedback ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_id INTEGER NOT NULL, "
            "vote INTEGER NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_feedback_job ON feedback(job_id)")
        _migrate_ownership(con)
        state_columns = {row["name"] for row in con.execute("PRAGMA table_info(job_states)")}
        for column in ("travel_key", "travel_json"):
            if column not in state_columns:
                con.execute(f"ALTER TABLE job_states ADD COLUMN {column} TEXT")
        _normalize_job_dates(con)
        _recover_raw_verdicts(con)
        _fill_search_text(con)
        migrate(con)



def _normalize_job_dates(con) -> None:
    """One-off idempotent rewrite to ISO-8601 UTC; empty and invalid become NULL."""
    if con.execute("SELECT 1 FROM meta WHERE key='posted_at_normalized_v2'").fetchone():
        return
    rows = con.execute("SELECT id, posted_at FROM jobs").fetchall()
    for row in rows:
        normalized = normalize_posted_at(row["posted_at"])
        if normalized != row["posted_at"]:
            con.execute("UPDATE jobs SET posted_at=? WHERE id=?", (normalized, row["id"]))
    con.execute("INSERT INTO meta(key,value) VALUES ('posted_at_normalized_v2','1')")



def _backup_before_upgrade() -> None:
    """Protect existing state before even the legacy schema setup can mutate it."""
    from ..migrations import MIGRATIONS

    if not config.DB_PATH.exists():
        return
    with closing(sqlite3.connect(config.DB_PATH)) as source:
        tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not tables:
            return
        current = int(source.execute("PRAGMA user_version").fetchone()[0])
        markers = (
            {row[0] for row in source.execute("SELECT key FROM meta")}
            if "meta" in tables
            else set()
        )
        required = {"ownership_v1", "posted_at_normalized_v2", "raw_verdicts_v1", "search_text_v1"}
        if current < len(MIGRATIONS) or not required <= markers:
            _backup_database(source, current)



def _backup_database(source: sqlite3.Connection, current: int) -> None:
    """Take a WAL-safe, uniquely named backup; failures abort startup."""
    directory = config.DATA_DIR / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    descriptor, filename = tempfile.mkstemp(
        prefix=f"app-{stamp}-v{current}-", suffix=".db", dir=directory
    )
    os.close(descriptor)
    target = config.DATA_DIR / "backups" / os.path.basename(filename)
    try:
        with closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    for old in sorted(directory.glob("app-*.db"), reverse=True)[5:]:
        old.unlink(missing_ok=True)



def migrate(con) -> None:
    from ..migrations import MIGRATIONS

    current = int(con.execute("PRAGMA user_version").fetchone()[0])
    if current >= len(MIGRATIONS):
        return
    con.commit()
    for index, module in enumerate(MIGRATIONS[current:], start=current + 1):
        con.execute("BEGIN IMMEDIATE")
        try:
            module.up(con)
            con.execute(f"PRAGMA user_version={index}")
            con.commit()
        except Exception:
            con.rollback()
            raise



def _migrate_ownership(con):
    """Add ownership without rewriting any existing personal values or files."""
    for table in ("sessions", "messages", "usage_log", "profile_versions", "feedback"):
        if "owner" not in {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN owner TEXT NOT NULL DEFAULT 'legacy'")
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_owner ON {table}(owner)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS user_meta(owner TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(owner,key))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS job_states(owner TEXT NOT NULL,job_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'new',vote INTEGER NOT NULL DEFAULT 0,match_score REAL,match_reason TEXT,profile_hash TEXT,rank_score REAL,typed_signals TEXT,signals_key TEXT,signal_confidence REAL,PRIMARY KEY(owner,job_id))"
    )
    if not con.execute("SELECT 1 FROM meta WHERE key='ownership_v1'").fetchone():
        con.execute(
            "INSERT INTO job_states SELECT 'legacy',id,status,COALESCE(vote,0),match_score,match_reason,profile_hash,rank_score,typed_signals,signals_key,signal_confidence FROM jobs"
        )
        con.execute(
            "INSERT INTO user_meta SELECT 'legacy',key,value FROM meta WHERE key NOT IN ('scan_status','scan_summary','token_df_total')"
        )
        con.execute("INSERT INTO meta VALUES ('ownership_v1','1')")
    if "owner" not in {row["name"] for row in con.execute("PRAGMA table_info(documents)")}:
        con.execute(
            "CREATE TABLE documents_owned(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,kind TEXT NOT NULL,version INTEGER NOT NULL,docx_path TEXT,pdf_path TEXT,meta_json TEXT,created_at TEXT NOT NULL,owner TEXT NOT NULL DEFAULT 'legacy',UNIQUE(owner,job_id,kind,version))"
        )
        con.execute("INSERT INTO documents_owned SELECT *, 'legacy' FROM documents")
        con.execute("DROP TABLE documents")
        con.execute("ALTER TABLE documents_owned RENAME TO documents")



def _search_text(title, company, description) -> str:
    return " ".join(
        " ".join(str(value or "").lower().split()) for value in (title, company, description)
    ).strip()



def _fill_search_text(con) -> None:
    if con.execute("SELECT 1 FROM meta WHERE key='search_text_v1'").fetchone():
        return
    rows = con.execute("SELECT id, title, company, description, search_text FROM jobs").fetchall()
    for row in rows:
        text = _search_text(row["title"], row["company"], row["description"])
        if text != row["search_text"]:
            con.execute("UPDATE jobs SET search_text=? WHERE id=?", (text, row["id"]))
    con.execute("INSERT INTO meta(key,value) VALUES ('search_text_v1','1')")



def _recover_raw_verdicts(con) -> None:
    """Older rows stored a rule+LLM blend; recover the raw LLM verdict once."""
    if con.execute("SELECT 1 FROM meta WHERE key='raw_verdicts_v1'").fetchone():
        return
    rows = con.execute(
        "SELECT owner, job_id, match_score, match_reason FROM job_states "
        "WHERE match_score IS NOT NULL AND match_reason IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            reason = json.loads(row["match_reason"])
            rule = float(reason["rule_score"])
        except (ValueError, TypeError, KeyError):
            continue
        raw = max(0.0, min(100.0, (float(row["match_score"]) - 0.4 * rule) / 0.6))
        con.execute(
            "UPDATE job_states SET match_score=? WHERE owner=? AND job_id=?",
            (round(raw, 1), row["owner"], row["job_id"]),
        )
    con.execute("INSERT INTO meta(key,value) VALUES ('raw_verdicts_v1','1')")

