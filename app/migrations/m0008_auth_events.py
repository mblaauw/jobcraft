"""Who signed in when, from where, and what an administrator did to an account.

Security bookkeeping only: a moment, a kind, the account, the address and the browser.
Nothing about what a person does inside the app. Rows for an account go into its export
and leave with it; failed attempts on unknown names belong to nobody and are pruned.
"""

from __future__ import annotations

import sqlite3


def up(con: sqlite3.Connection) -> None:
    # Caller owns BEGIN/COMMIT; no executescript (which can implicitly commit).
    con.execute(
        "CREATE TABLE IF NOT EXISTS auth_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "at TEXT NOT NULL, "
        "kind TEXT NOT NULL, "
        "user_id TEXT, "
        "username TEXT, "
        "ip TEXT, "
        "user_agent TEXT, "
        "actor_id TEXT)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS auth_events_at ON auth_events(at)")
    con.execute("CREATE INDEX IF NOT EXISTS auth_events_user ON auth_events(user_id, id)")
