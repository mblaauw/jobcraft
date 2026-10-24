"""SQLite accounts operations."""
from __future__ import annotations

import json
import sqlite3

from .. import tenancy
from .connection import connect, now


def add_profile_version(path: str, note: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO profile_versions(created_at, path, note,owner) VALUES (?,?,?,?)",
            (now(), path, note, tenancy.user_id()),
        )



def list_profile_versions(limit: int = 30) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM profile_versions WHERE owner=? ORDER BY id DESC LIMIT ?",
            (tenancy.user_id(), limit),
        ).fetchall()



def get_profile(owner: str | None = None) -> str | None:
    with connect() as con:
        row = con.execute(
            "SELECT profile_md FROM profiles WHERE owner=?", (owner or tenancy.user_id(),)
        ).fetchone()
    return row["profile_md"] if row else None



def set_profile(profile_md: str, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO profiles(owner, profile_md, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET profile_md=excluded.profile_md, "
            "updated_at=excluded.updated_at",
            (owner or tenancy.user_id(), profile_md, now()),
        )



def get_preferences(owner: str | None = None) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT data_json FROM preferences WHERE owner=?", (owner or tenancy.user_id(),)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except ValueError:
        return None



def set_preferences(data: dict, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO preferences(owner, data_json, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data_json=excluded.data_json, "
            "updated_at=excluded.updated_at",
            (owner or tenancy.user_id(), json.dumps(data, ensure_ascii=False), now()),
        )



def get_feedback_profile(owner: str | None = None) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT data_json FROM feedback_profiles WHERE owner=?",
            (owner or tenancy.user_id(),),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except ValueError:
        return None



def set_feedback_profile(data: dict, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO feedback_profiles(owner, data_json, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data_json=excluded.data_json, "
            "updated_at=excluded.updated_at",
            (owner or tenancy.user_id(), json.dumps(data, ensure_ascii=False), now()),
        )



def get_interview_state(owner: str | None = None) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT data_json FROM interview_state WHERE owner=?",
            (owner or tenancy.user_id(),),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except ValueError:
        return None



def set_interview_state(data: dict, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO interview_state(owner, data_json, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data_json=excluded.data_json, "
            "updated_at=excluded.updated_at",
            (owner or tenancy.user_id(), json.dumps(data, ensure_ascii=False), now()),
        )



def append_answer(question: str, answer: str, session_id: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO answers(owner, session_id, question, answer, created_at) "
            "VALUES (?,?,?,?,?)",
            (tenancy.user_id(), session_id, question, answer, now()),
        )



def list_answers(owner: str | None = None) -> list:
    with connect() as con:
        return con.execute(
            "SELECT * FROM answers WHERE owner=? ORDER BY id",
            (owner or tenancy.user_id(),),
        ).fetchall()



def active_owners_with_profile() -> list[str]:
    with connect() as con:
        rows = con.execute(
            "SELECT u.id FROM users u JOIN profiles p ON p.owner=u.id WHERE u.active=1"
        ).fetchall()
    return [row["id"] for row in rows]

