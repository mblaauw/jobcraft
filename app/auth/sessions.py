"""Local authentication: sessions."""
from __future__ import annotations

import hashlib
import os
import secrets
from contextlib import closing
from datetime import UTC, datetime, timedelta

from fastapi import Request
from fastapi.responses import Response

from .. import config, store
from ..util import now_iso
from .constants import COOKIE_NAME, LAST_SEEN_REFRESH_SECONDS
from .timestamps import _expired, _iso, _parse_ts
from .users import _get_user_by_id, _public_user


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()



def _create_session(
    user_id: str | None, ttl: int, user_agent: str | None = None
) -> tuple[str, str]:
    """Return ``(raw_token, csrf_token)``; only the SHA256 digest is stored."""
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with closing(store.connect()) as con, con:
        con.execute(
            "INSERT INTO auth_sessions"
            " (token_hash, user_id, csrf_token, created_at, last_seen_at, expires_at, revoked,"
            " user_agent) VALUES (?,?,?,?,?,?,0,?)",
            (
                _hash_token(token),
                user_id,
                csrf_token,
                _iso(now),
                _iso(now),
                _iso(now + timedelta(seconds=ttl)),
                (user_agent or "")[:200],
            ),
        )
    return token, csrf_token



def _session_row(token_hash: str):
    with closing(store.connect()) as con:
        return con.execute(
            "SELECT * FROM auth_sessions WHERE token_hash=?", (token_hash,)
        ).fetchone()



def _touch_session(token_hash: str, last_seen: str) -> None:
    parsed = _parse_ts(last_seen)
    if parsed and (datetime.now(UTC) - parsed).total_seconds() < LAST_SEEN_REFRESH_SECONDS:
        return
    with closing(store.connect()) as con, con:
        con.execute(
            "UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?", (now_iso(), token_hash)
        )



def _revoke_session(token_hash: str) -> None:
    with closing(store.connect()) as con, con:
        con.execute("UPDATE auth_sessions SET revoked=1 WHERE token_hash=?", (token_hash,))



def _delete_session(token_hash: str) -> None:
    with closing(store.connect()) as con, con:
        con.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))



def _revoke_sessions_for(user_id: str) -> int:
    with closing(store.connect()) as con, con:
        cursor = con.execute(
            "UPDATE auth_sessions SET revoked=1 WHERE user_id=? AND revoked=0", (user_id,)
        )
    return cursor.rowcount or 0



def _revoke_other_sessions(user_id: str, keep_token_hash: str | None) -> int:
    with closing(store.connect()) as con, con:
        cursor = con.execute(
            "UPDATE auth_sessions SET revoked=1"
            " WHERE user_id=? AND revoked=0 AND token_hash IS NOT ?",
            (user_id, keep_token_hash or ""),
        )
    return cursor.rowcount or 0



def _rotate_csrf(token_hash: str) -> str | None:
    csrf_token = secrets.token_urlsafe(32)
    with closing(store.connect()) as con, con:
        cursor = con.execute(
            "UPDATE auth_sessions SET csrf_token=? WHERE token_hash=?", (csrf_token, token_hash)
        )
    return csrf_token if cursor.rowcount else None



def _session_for_request(request: Request) -> tuple[dict | None, dict | None, bool]:
    """Resolve the cookie into ``(session, public_user, invalid_cookie)``."""
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None, None, False
    token_hash = _hash_token(token)
    row = _session_row(token_hash)
    if row is None:
        return None, None, True
    if row["revoked"] or _expired(row["expires_at"]):
        _delete_session(token_hash)
        return None, None, True
    if row["user_id"] is None:
        _touch_session(token_hash, row["last_seen_at"])
        return dict(row), None, False
    user = _get_user_by_id(row["user_id"])
    if user is None or not user["active"]:
        _revoke_session(token_hash)
        return None, None, True
    _touch_session(token_hash, row["last_seen_at"])
    return dict(row), _public_user(user), False



def _cookie_secure() -> bool:
    """Optional hardening: APP_COOKIE_SECURE=1 behind TLS. Defaults to off (localhost)."""
    value = getattr(config, "APP_COOKIE_SECURE", None)
    if value is None:
        value = os.environ.get("APP_COOKIE_SECURE", "")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")



def _set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        path="/",
        secure=_cookie_secure(),
    )



def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=_cookie_secure()
    )

