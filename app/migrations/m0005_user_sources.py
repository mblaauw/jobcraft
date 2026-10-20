"""Per-account source enablement."""

from __future__ import annotations

import sqlite3


def up(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_sources (
            owner TEXT NOT NULL,
            source_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (owner, source_id)
        );
        """
    )
