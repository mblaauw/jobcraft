"""Structured per-account state; files remain as an export/import surface."""

from __future__ import annotations

import sqlite3

TABLES = """
CREATE TABLE IF NOT EXISTS profiles (
    owner TEXT PRIMARY KEY,
    profile_md TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
    owner TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    session_id TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    consolidated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_answers_owner ON answers(owner, id);
CREATE TABLE IF NOT EXISTS profile_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    profile_md TEXT NOT NULL,
    preferences_json TEXT NOT NULL,
    meta_json TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_profile_drafts_owner ON profile_drafts(owner, id);
CREATE TABLE IF NOT EXISTS feedback_profiles (
    owner TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interview_state (
    owner TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_configs (
    id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
"""


def up(con: sqlite3.Connection) -> None:
    con.executescript(TABLES)
