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



def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con

