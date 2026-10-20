"""SQLite persistence. Forward-compatible schema for later phases."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from . import config, tenancy


def now() -> str:
    return datetime.now(UTC).isoformat()


def stale_cutoff() -> str:
    try:
        days = int(os.environ.get("JOB_STALE_DAYS", "21"))
    except ValueError:
        days = 21
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


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


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db() -> None:
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


def _backup_database() -> None:
    from .migrations import MIGRATIONS

    directory = config.DATA_DIR / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"app-{stamp}-pre-v{len(MIGRATIONS)}.db"
    source = sqlite3.connect(config.DB_PATH)
    try:
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    finally:
        source.close()
    for old in sorted(directory.glob("app-*.db"), reverse=True)[5:]:
        old.unlink(missing_ok=True)


def migrate(con) -> None:
    from .migrations import MIGRATIONS

    current = int(con.execute("PRAGMA user_version").fetchone()[0])
    if current >= len(MIGRATIONS):
        return
    if current == 0 and con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] > 0:
        _backup_database()
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


def recover_interrupted_work() -> None:
    """Repair interrupted jobs for every owner, without opening personal files."""
    with connect() as con:
        con.execute(
            "UPDATE meta SET value='interrupted' WHERE key='scan_status' AND value='running'"
        )
        con.execute(
            "UPDATE user_meta SET value='interrupted' WHERE key IN ('bootstrap_status','match_status') AND value='running'"
        )
        for row in con.execute(
            "SELECT owner,key,value FROM user_meta WHERE key LIKE 'doc_status:%'"
        ).fetchall():
            try:
                state = json.loads(row["value"])
            except (ValueError, TypeError):
                continue
            if isinstance(state, dict) and state.get("state") == "running":
                state.update(
                    state="error",
                    error="Genereren is onderbroken door een herstart. Probeer opnieuw.",
                )
                con.execute(
                    "UPDATE user_meta SET value=? WHERE owner=? AND key=?",
                    (json.dumps(state), row["owner"], row["key"]),
                )


def _job_select(owner: str | None = None) -> str:
    owner = owner or tenancy.user_id()
    public = "id,source_id,url,title,company,location,hours,description,posted_at,first_seen,last_seen,content_hash,lat,lon,search_text".split(
        ","
    )
    defaults = {
        "status": "'new'",
        "vote": "0",
        "match_score": "NULL",
        "match_reason": "NULL",
        "profile_hash": "NULL",
        "rank_score": "NULL",
        "typed_signals": "NULL",
        "signals_key": "NULL",
        "signal_confidence": "NULL",
    }
    fields = ["j." + name for name in public]
    for name, default in defaults.items():
        fallback = "j." + name if owner == tenancy.LEGACY_ID else default
        fields.append(f"COALESCE(s.{name},{fallback}) AS {name}")
    return (
        "SELECT "
        + ",".join(fields)
        + " FROM jobs j LEFT JOIN job_states s ON s.job_id=j.id AND s.owner=?"
    )


def _set_job_state(con, job_id, **fields):
    names = list(fields)
    con.execute(
        "INSERT INTO job_states(owner,job_id,"
        + ",".join(names)
        + ") VALUES ("
        + ",".join("?" for _ in range(len(names) + 2))
        + ") ON CONFLICT(owner,job_id) DO UPDATE SET "
        + ",".join(f"{name}=excluded.{name}" for name in names),
        (tenancy.user_id(), job_id, *fields.values()),
    )


def create_session(session_id: str, title: str, agent: str, model: str) -> None:
    stamp = now()
    with connect() as con:
        con.execute(
            "INSERT INTO sessions(id, title, agent, model, created_at, updated_at,owner) "
            "VALUES (?,?,?,?,?,?,?)",
            (session_id, title, agent, model, stamp, stamp, tenancy.user_id()),
        )


def update_session(session_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    columns = ", ".join(f"{key}=?" for key in fields)
    with connect() as con:
        con.execute(
            f"UPDATE sessions SET {columns} WHERE id=? AND owner=?",
            (*fields.values(), session_id, tenancy.user_id()),
        )


def add_session_usage(
    session_id: str, input_tokens: int, output_tokens: int, cost_usd: float
) -> None:
    with connect() as con:
        con.execute(
            "UPDATE sessions SET cost_usd=cost_usd+?, tokens_in=tokens_in+?, "
            "tokens_out=tokens_out+?, updated_at=? WHERE id=? AND owner=?",
            (
                float(cost_usd),
                int(input_tokens),
                int(output_tokens),
                now(),
                session_id,
                tenancy.user_id(),
            ),
        )


def get_session(session_id: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM sessions WHERE id=? AND owner=?", (session_id, tenancy.user_id())
        ).fetchone()


def list_sessions(limit: int = 30) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM sessions WHERE owner=? ORDER BY updated_at DESC LIMIT ?",
            (tenancy.user_id(), limit),
        ).fetchall()


def add_message(
    session_id: str,
    opencode_message_id: str,
    role: str,
    text: str,
    model: str | None = None,
    hidden: int = 0,
) -> bool:
    with connect() as con:
        cursor = con.execute(
            "INSERT OR IGNORE INTO messages"
            "(session_id, opencode_message_id, role, text, model, hidden, created_at,owner) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (session_id, opencode_message_id, role, text, model, hidden, now(), tenancy.user_id()),
        )
        return cursor.rowcount > 0


def list_messages(session_id: str) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM messages WHERE session_id=? AND owner=? ORDER BY id ASC",
            (session_id, tenancy.user_id()),
        ).fetchall()


def record_usage(
    session_id: str,
    kind: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    agent: str | None = None,
) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO usage_log"
            "(session_id, kind, input_tokens, output_tokens, cost_usd, created_at, agent,owner) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                session_id,
                kind,
                input_tokens,
                output_tokens,
                cost_usd,
                now(),
                agent,
                tenancy.user_id(),
            ),
        )


def update_job_signals(
    job_id: int,
    rank_score: float,
    signals_json: str,
    signals_key: str,
    confidence: float,
) -> None:
    with connect() as con:
        _set_job_state(
            con,
            job_id,
            rank_score=rank_score,
            typed_signals=signals_json,
            signals_key=signals_key,
            signal_confidence=confidence,
        )


def get_job_travel(job_id: int, key: str) -> str | None:
    with connect() as con:
        row = con.execute(
            "SELECT travel_json FROM job_states WHERE owner=? AND job_id=? AND travel_key=?",
            (tenancy.user_id(), job_id, key),
        ).fetchone()
    return row["travel_json"] if row else None


def set_job_travel(job_id: int, key: str, payload: str) -> None:
    with connect() as con:
        _set_job_state(con, job_id, travel_key=key, travel_json=payload)


def set_rank_scores(scores: dict[int, float]) -> None:
    """Persist deterministic rank scores for many jobs in one transaction."""
    if not scores:
        return
    with connect() as con:
        for job_id, score in scores.items():
            _set_job_state(con, job_id, rank_score=score)


def usage_total() -> dict:
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS tin, "
            "COALESCE(SUM(output_tokens),0) AS tout, "
            "COALESCE(SUM(cost_usd),0) AS cost, COUNT(*) AS calls FROM usage_log WHERE owner=?",
            (tenancy.user_id(),),
        ).fetchone()
    return dict(row)


def add_profile_version(path: str, note: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO profile_versions(created_at, path, note,owner) VALUES (?,?,?,?)",
            (now(), path, note, tenancy.user_id()),
        )


def list_profile_versions(limit: int = 30) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM profile_versions WHERE owner=? ORDER BY id DESC LIMIT ?",
            (tenancy.user_id(), limit),
        ).fetchall()


def get_profile(owner: str | None = None) -> str | None:
    with connect() as con:
        row = con.execute(
            "SELECT profile_md FROM profiles WHERE owner=?", (owner or tenancy.user_id(),)
        ).fetchone()
    return row["profile_md"] if row else None


def set_profile(profile_md: str, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO profiles(owner, profile_md, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET profile_md=excluded.profile_md, "
            "updated_at=excluded.updated_at",
            (owner or tenancy.user_id(), profile_md, now()),
        )


def get_preferences(owner: str | None = None) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT data_json FROM preferences WHERE owner=?", (owner or tenancy.user_id(),)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except ValueError:
        return None


def set_preferences(data: dict, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO preferences(owner, data_json, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data_json=excluded.data_json, "
            "updated_at=excluded.updated_at",
            (owner or tenancy.user_id(), json.dumps(data, ensure_ascii=False), now()),
        )


def get_feedback_profile(owner: str | None = None) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT data_json FROM feedback_profiles WHERE owner=?",
            (owner or tenancy.user_id(),),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except ValueError:
        return None


def set_feedback_profile(data: dict, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO feedback_profiles(owner, data_json, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data_json=excluded.data_json, "
            "updated_at=excluded.updated_at",
            (owner or tenancy.user_id(), json.dumps(data, ensure_ascii=False), now()),
        )


def get_interview_state(owner: str | None = None) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT data_json FROM interview_state WHERE owner=?",
            (owner or tenancy.user_id(),),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except ValueError:
        return None


def set_interview_state(data: dict, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO interview_state(owner, data_json, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data_json=excluded.data_json, "
            "updated_at=excluded.updated_at",
            (owner or tenancy.user_id(), json.dumps(data, ensure_ascii=False), now()),
        )


def append_answer(question: str, answer: str, session_id: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO answers(owner, session_id, question, answer, created_at) "
            "VALUES (?,?,?,?,?)",
            (tenancy.user_id(), session_id, question, answer, now()),
        )


def list_answers(owner: str | None = None) -> list:
    with connect() as con:
        return con.execute(
            "SELECT * FROM answers WHERE owner=? ORDER BY id",
            (owner or tenancy.user_id(),),
        ).fetchall()


def load_token_df() -> tuple[dict[str, int], int] | None:
    with connect() as con:
        rows = con.execute("SELECT token, n FROM token_df").fetchall()
        total = con.execute("SELECT value FROM meta WHERE key='token_df_total'").fetchone()
    if not rows or total is None:
        return None
    return {row["token"]: int(row["n"]) for row in rows}, int(total["value"])


def save_token_df(counter: dict[str, int], total: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM token_df")
        con.executemany(
            "INSERT INTO token_df(token, n) VALUES (?, ?)",
            [(token, int(count)) for token, count in counter.items()],
        )
        con.execute(
            "INSERT INTO meta(key,value) VALUES ('token_df_total',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(total),),
        )


def meta_get(key: str, default: str | None = None) -> str | None:
    with connect() as con:
        if key in ("scan_status", "scan_summary", "ownership_v1", "token_df_total"):
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        else:
            row = con.execute(
                "SELECT value FROM user_meta WHERE owner=? AND key=?", (tenancy.user_id(), key)
            ).fetchone()
    return row["value"] if row else default


def meta_set(key: str, value: str) -> None:
    with connect() as con:
        if key in ("scan_status", "scan_summary", "ownership_v1", "token_df_total"):
            con.execute(
                "INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        else:
            con.execute(
                "INSERT INTO user_meta(owner,key,value) VALUES (?,?,?) ON CONFLICT(owner,key) DO UPDATE SET value=excluded.value",
                (tenancy.user_id(), key, value),
            )


# ---------------------------------------------------------------- phase 2


def load_source_configs() -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT config_json, enabled FROM source_configs ORDER BY id").fetchall()
    configs = []
    for row in rows:
        try:
            entry = json.loads(row["config_json"])
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id"):
            entry["enabled"] = bool(row["enabled"])
            configs.append(entry)
    return configs


def save_source_config(entry: dict, enabled: bool | None = None) -> None:
    source_id = entry.get("id")
    if not source_id:
        return
    state = bool(entry.get("enabled", True)) if enabled is None else enabled
    with connect() as con:
        con.execute(
            "INSERT INTO source_configs(id, config_json, enabled, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json, "
            "enabled=excluded.enabled, updated_at=excluded.updated_at",
            (source_id, json.dumps(entry, ensure_ascii=False), 1 if state else 0, now()),
        )


def set_source_config_enabled(source_id: str, enabled: bool) -> bool:
    with connect() as con:
        cursor = con.execute(
            "UPDATE source_configs SET enabled=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, now(), source_id),
        )
    return cursor.rowcount > 0


def sync_sources(entries: list[dict]) -> None:
    with connect() as con:
        for entry in entries:
            con.execute(
                "INSERT INTO sources(id, name, url, kind, config_json, enabled, notes) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, "
                "kind=excluded.kind, config_json=excluded.config_json, notes=excluded.notes, "
                "enabled=excluded.enabled",
                (
                    entry["id"],
                    entry.get("name") or entry["id"],
                    entry.get("base_url"),
                    entry.get("kind"),
                    json.dumps(entry, ensure_ascii=False),
                    1 if entry.get("enabled", True) else 0,
                    entry.get("notes"),
                ),
            )


def list_sources() -> list:
    with connect() as con:
        return con.execute("SELECT * FROM sources ORDER BY name").fetchall()


def update_source_status(source_id: str, status: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE sources SET last_scan_at=?, last_status=? WHERE id=?",
            (now(), status, source_id),
        )


def upsert_job(job: dict) -> tuple[int, bool]:
    stamp = now()
    job = {**job, "posted_at": normalize_posted_at(job.get("posted_at"))}
    search_text = _search_text(job.get("title"), job.get("company"), job.get("description"))
    with connect() as con:
        existing = con.execute("SELECT id FROM jobs WHERE url=?", (job["url"],)).fetchone()
        if existing:
            con.execute(
                "UPDATE jobs SET source_id=?, title=?, company=?, location=?, hours=?, "
                "description=?, posted_at=?, last_seen=?, content_hash=?, lat=?, lon=?, "
                "search_text=? WHERE id=?",
                (
                    job.get("source_id"),
                    job.get("title"),
                    job.get("company"),
                    job.get("location"),
                    job.get("hours"),
                    job.get("description"),
                    job.get("posted_at"),
                    stamp,
                    job.get("content_hash"),
                    job.get("lat"),
                    job.get("lon"),
                    search_text,
                    existing["id"],
                ),
            )
            return existing["id"], False
        cursor = con.execute(
            "INSERT INTO jobs(source_id, url, title, company, location, hours, description, "
            "posted_at, first_seen, last_seen, content_hash, lat, lon, search_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.get("source_id"),
                job["url"],
                job.get("title"),
                job.get("company"),
                job.get("location"),
                job.get("hours"),
                job.get("description"),
                job.get("posted_at"),
                stamp,
                stamp,
                job.get("content_hash"),
                job.get("lat"),
                job.get("lon"),
                search_text,
            ),
        )
        return cursor.lastrowid, True


def set_job_vote(job_id: int, vote: int) -> None:
    """Store the account's up/down vote (1, -1 or 0) plus a learning event."""
    with connect() as con:
        _set_job_state(con, job_id, vote=vote)
        con.execute(
            "INSERT INTO feedback(job_id, vote, created_at,owner) VALUES (?,?,?,?)",
            (job_id, vote, now(), tenancy.user_id()),
        )


def list_votes() -> dict:
    """Jobs this account voted on, split into up/down, for the feedback learner."""
    with connect() as con:
        rows = con.execute(
            "SELECT id,title,company,source_id,description,vote FROM ("
            + _job_select()
            + ") WHERE vote IS NOT NULL AND vote != 0 ORDER BY id DESC",
            (tenancy.user_id(),),
        ).fetchall()
    return {
        "up": [row for row in rows if row["vote"] > 0],
        "down": [row for row in rows if row["vote"] < 0],
    }


def parse_job_date(raw) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    try:
        try:
            parsed = datetime.fromisoformat(str(raw).strip())
        except ValueError:
            parsed = parsedate_to_datetime(str(raw).strip())
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_posted_at(raw) -> str | None:
    """Store one comparable ISO-8601 UTC form; absent or invalid becomes NULL."""
    parsed = parse_job_date(raw)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def job_date(job) -> datetime:
    """Publication date, otherwise discovery date; feeds may use ISO or RFC dates."""
    for raw in (job["posted_at"], job["first_seen"]):
        parsed = parse_job_date(raw)
        if parsed:
            return parsed
    return datetime.min.replace(tzinfo=UTC)


def list_jobs(
    limit: int | None = 200,
    status: str | None = None,
    query_text: str | None = None,
    source: str | None = None,
    sort: str = "rank",
    include_stale: bool = False,
) -> list:
    clauses: list[str] = []
    params: list = [tenancy.user_id()]
    if status:
        clauses.append("status=?")
        params.append(status)
    if source:
        clauses.append("source_id=?")
        params.append(source)
    if query_text:
        like = f"%{query_text}%"
        clauses.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        params.extend([like, like, like])
    if not include_stale:
        clauses.append(
            "NOT (last_seen < ? AND status NOT IN ('saved','applied') "
            "AND EXISTS (SELECT 1 FROM sources src WHERE src.id=source_id "
            "AND src.last_status='ok' AND src.last_scan_at > last_seen))"
        )
        params.append(stale_cutoff())
    sql = "SELECT * FROM (" + _job_select() + ")"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # Only internal SQL fragments, never user-supplied column names.
    if sort == "newest":
        sql += " ORDER BY COALESCE(posted_at, first_seen) DESC, id DESC"
    else:
        sql += " ORDER BY COALESCE(rank_score, match_score, -1) DESC, COALESCE(posted_at, first_seen) DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as con:
        return con.execute(sql, tuple(params)).fetchall()


def get_job(job_id: int):
    with connect() as con:
        return con.execute(
            "SELECT * FROM (" + _job_select() + ") WHERE id=?", (tenancy.user_id(), job_id)
        ).fetchone()


def list_scored_jobs(owner: str | None = None) -> list:
    """Owner-scoped rows for offline quality checks, not for request handlers."""
    owner = owner or tenancy.user_id()
    with connect() as con:
        return con.execute(
            "SELECT id, rank_score, match_score, typed_signals FROM ("
            + _job_select(owner)
            + ") WHERE match_score IS NOT NULL AND rank_score IS NOT NULL",
            (owner,),
        ).fetchall()


def update_job_status(job_id: int, status: str) -> None:
    with connect() as con:
        _set_job_state(con, job_id, status=status)


def list_jobs_for_matching(limit: int = 2000) -> list:
    with connect() as con:
        return con.execute(
            "SELECT * FROM ("
            + _job_select()
            + ") WHERE NOT (last_seen < ? AND status NOT IN ('saved','applied') "
            "AND EXISTS (SELECT 1 FROM sources src WHERE src.id=source_id "
            "AND src.last_status='ok' AND src.last_scan_at > last_seen)) "
            "ORDER BY COALESCE(posted_at, first_seen) DESC, id DESC LIMIT ?",
            (tenancy.user_id(), stale_cutoff(), limit),
        ).fetchall()


def update_job_match(job_id: int, score: float, reason: str, profile_hash: str) -> None:
    with connect() as con:
        _set_job_state(
            con, job_id, match_score=score, match_reason=reason, profile_hash=profile_hash
        )


def add_document(
    job_id: int,
    kind: str,
    version: int,
    docx_path: str,
    pdf_path: str | None,
    meta_json: str,
) -> int:
    with connect() as con:
        cursor = con.execute(
            "INSERT INTO documents(job_id, kind, version, docx_path, pdf_path, meta_json, created_at,owner) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, kind, version, docx_path, pdf_path, meta_json, now(), tenancy.user_id()),
        )
        return cursor.lastrowid


def list_documents(job_id: int) -> list:
    with connect() as con:
        return con.execute(
            "SELECT * FROM documents WHERE job_id=? AND owner=? ORDER BY kind, version DESC",
            (job_id, tenancy.user_id()),
        ).fetchall()


def get_document(document_id: int):
    with connect() as con:
        return con.execute(
            "SELECT * FROM documents WHERE id=? AND owner=?", (document_id, tenancy.user_id())
        ).fetchone()


def count_jobs() -> dict:
    with connect() as con:
        total = con.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        view = "(" + _job_select() + ")"
        fresh = con.execute(
            "SELECT COUNT(*) AS n FROM " + view + " WHERE status='new'", (tenancy.user_id(),)
        ).fetchone()["n"]
        matched = con.execute(
            "SELECT COUNT(*) AS n FROM " + view + " WHERE match_score IS NOT NULL",
            (tenancy.user_id(),),
        ).fetchone()["n"]
        signaled = con.execute(
            "SELECT COUNT(*) AS n FROM " + view + " WHERE typed_signals IS NOT NULL",
            (tenancy.user_id(),),
        ).fetchone()["n"]
    return {
        "total": total,
        "new": fresh,
        "matched": matched,
        "signaled": signaled,
        "unmatched": total - matched,
    }


def count_jobs_by_source() -> dict[str, int]:
    with connect() as con:
        rows = con.execute(
            "SELECT COALESCE(source_id, '?') AS source_id, COUNT(*) AS n FROM jobs GROUP BY source_id"
        ).fetchall()
    return {row["source_id"]: row["n"] for row in rows}


def count_documents() -> int:
    with connect() as con:
        return con.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE owner=?", (tenancy.user_id(),)
        ).fetchone()["n"]
