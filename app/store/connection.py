"""SQLite connection operations."""
from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta

from .. import config


def now() -> str:
    return datetime.now(UTC).isoformat()



def stale_cutoff() -> str:
    try:
        days = int(os.environ.get("JOB_STALE_DAYS", "21"))
    except ValueError:
        days = 21
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()



class Connection(sqlite3.Connection):
    """Transaction context with deterministic closure, including failed commits."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, factory=Connection, timeout=5.0)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con
    except Exception:
        con.close()
        raise
