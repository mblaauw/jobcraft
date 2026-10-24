"""SQLite recovery operations."""
from __future__ import annotations

import json

from .connection import connect


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

