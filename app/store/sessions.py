"""SQLite sessions operations."""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import tenancy
from .connection import connect, now


def create_session(session_id: str, title: str, agent: str, model: str) -> None:
    stamp = now()
    with connect() as con:
        con.execute(
            "INSERT INTO sessions(id, title, agent, model, created_at, updated_at,owner) "
            "VALUES (?,?,?,?,?,?,?)",
            (session_id, title, agent, model, stamp, stamp, tenancy.user_id()),
        )



def update_session(session_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    columns = ", ".join(f"{key}=?" for key in fields)
    with connect() as con:
        con.execute(
            f"UPDATE sessions SET {columns} WHERE id=? AND owner=?",
            (*fields.values(), session_id, tenancy.user_id()),
        )



def add_session_usage(
    session_id: str, input_tokens: int, output_tokens: int, cost_usd: float
) -> None:
    with connect() as con:
        con.execute(
            "UPDATE sessions SET cost_usd=cost_usd+?, tokens_in=tokens_in+?, "
            "tokens_out=tokens_out+?, updated_at=? WHERE id=? AND owner=?",
            (
                float(cost_usd),
                int(input_tokens),
                int(output_tokens),
                now(),
                session_id,
                tenancy.user_id(),
            ),
        )



def get_session(session_id: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM sessions WHERE id=? AND owner=?", (session_id, tenancy.user_id())
        ).fetchone()



def list_sessions(limit: int = 30) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM sessions WHERE owner=? ORDER BY updated_at DESC LIMIT ?",
            (tenancy.user_id(), limit),
        ).fetchall()



def add_message(
    session_id: str,
    opencode_message_id: str,
    role: str,
    text: str,
    model: str | None = None,
    hidden: int = 0,
) -> bool:
    with connect() as con:
        cursor = con.execute(
            "INSERT OR IGNORE INTO messages"
            "(session_id, opencode_message_id, role, text, model, hidden, created_at,owner) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (session_id, opencode_message_id, role, text, model, hidden, now(), tenancy.user_id()),
        )
        return cursor.rowcount > 0



def list_messages(session_id: str) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM messages WHERE session_id=? AND owner=? ORDER BY id ASC",
            (session_id, tenancy.user_id()),
        ).fetchall()

