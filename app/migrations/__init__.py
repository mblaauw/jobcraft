"""Numbered schema migrations; each module exposes ``up(con)``."""

from __future__ import annotations

from . import m0001_baseline, m0002_state_tables, m0003_files_to_db, m0004_tasks

MIGRATIONS = [m0001_baseline, m0002_state_tables, m0003_files_to_db, m0004_tasks]
