"""Durable background task queue."""

from __future__ import annotations

import sqlite3

SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_active_dedupe
    ON tasks(dedupe_key) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS tasks_owner_kind ON tasks(owner, kind, id DESC);
"""


def up(con: sqlite3.Connection) -> None:
    con.executescript(SQL)
