"""Local authentication: users."""
from __future__ import annotations

from contextlib import closing

from .. import store
from ..util import now_iso
from .constants import ROLE_ADMIN, ROLE_USER, ROLES
from .credentials import hash_password


def _public_user(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": row["role"] if row["role"] in ROLES else ROLE_USER,
    }



def _get_user_by_id(user_id: str):
    with closing(store.connect()) as con:
        return con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()



def _get_user_by_username(username: str):
    if not username:
        return None
    with closing(store.connect()) as con:
        return con.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()



def _insert_user(user_id: str, username: str, display_name: str, role: str, password: str) -> None:
    with closing(store.connect()) as con, con:
        con.execute(
            "INSERT INTO users (id, username, display_name, role, password_hash, created_at, active)"
            " VALUES (?,?,?,?,?,?,1)",
            (user_id, username, display_name, role, hash_password(password), now_iso()),
        )



def _set_password(user_id: str, password: str) -> None:
    with closing(store.connect()) as con, con:
        con.execute(
            "UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id)
        )



def _set_active(user_id: str, active: bool) -> None:
    with closing(store.connect()) as con, con:
        con.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))



def _deactivate_without_losing_last_admin(user_id: str) -> str | None:
    """Deactivate atomically; returns ``last_admin`` when that would leave none."""
    with closing(store.connect()) as con, con:
        con.execute("BEGIN IMMEDIATE")
        target = con.execute("SELECT role, active FROM users WHERE id=?", (user_id,)).fetchone()
        if target is None:
            return "missing"
        if target["role"] == ROLE_ADMIN and target["active"]:
            active_admins = con.execute(
                "SELECT COUNT(*) AS total FROM users WHERE role=? AND active=1", (ROLE_ADMIN,)
            ).fetchone()["total"]
            if active_admins <= 1:
                return "last_admin"
        con.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
    return None



def _mark_login(user_id: str) -> None:
    with closing(store.connect()) as con, con:
        con.execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), user_id))



def _list_users() -> list:
    with closing(store.connect()) as con:
        return list(con.execute("SELECT * FROM users ORDER BY role DESC, username").fetchall())



def _session_count(user_id: str) -> int:
    with closing(store.connect()) as con:
        row = con.execute(
            "SELECT COUNT(*) AS total FROM auth_sessions WHERE user_id=? AND revoked=0 AND expires_at > ?",
            (user_id, now_iso()),
        ).fetchone()
    return int(row["total"] or 0)

