"""Baseline: existing SCHEMA and legacy ALTERs are created by store.init_db."""

from __future__ import annotations

import sqlite3


def up(con: sqlite3.Connection) -> None:
    con.execute("SELECT 1")
