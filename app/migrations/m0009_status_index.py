"""Find an account's vacancies by status without scanning the catalogue.

Sollicitaties lists each status column separately; at 25,000 vacancies each list was a
full scan with a temporary sort (24.5 ms, five per page). Only rows that left "new" are
indexed: they are the few the board asks for. Adds an index; changes no data.
"""

from __future__ import annotations

import sqlite3


def up(con: sqlite3.Connection) -> None:
    # Caller owns BEGIN/COMMIT; no executescript (which can implicitly commit).
    columns = {row[1] for row in con.execute("PRAGMA table_info(job_states)")}
    if "status" not in columns:  # a stripped-down schema (old test databases): nothing to index
        return
    con.execute(
        "CREATE INDEX IF NOT EXISTS job_states_status ON job_states(owner, status) WHERE status != 'new'"
    )
