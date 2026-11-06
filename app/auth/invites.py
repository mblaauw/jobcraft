"""One-time invitation links: the admin never handles the new user's password."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import store
from ..web import templates
from . import activity
from .constants import (
    _USERNAME_RE,
    ANONYMOUS_TTL_SECONDS,
    CSRF_FIELD,
    MIN_PASSWORD_LENGTH,
    ROLE_USER,
    SESSION_TTL_SECONDS,
    log,
    router,
)
from .security import current_user, require_admin
from .sessions import _create_session, _revoke_session, _session_row, _set_session_cookie
from .users import _get_user_by_username, _insert_user, _mark_login
from .views import _start_anonymous_session, _users_response

INVITE_DAYS = 7
_PREFIX_RE = re.compile(r"/[A-Za-z0-9._/-]*")


def _key(token: str) -> str:
    return "invite:" + hashlib.sha256(token.encode()).hexdigest()


def _open(value: str | None) -> bool:
    try:
        return bool(value) and datetime.fromisoformat(json.loads(value)["expires"]) > datetime.now(UTC)
    except (ValueError, KeyError, TypeError):
        return False


def create_invite() -> str:
    """Only a hash is stored, so a copy of the database holds no usable link."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=INVITE_DAYS)
    with closing(store.connect()) as con, con:
        for row in con.execute("SELECT key, value FROM meta WHERE key LIKE 'invite:%'").fetchall():
            if not _open(row["value"]):
                con.execute("DELETE FROM meta WHERE key=?", (row["key"],))
        con.execute(
            "INSERT INTO meta(key,value) VALUES (?,?)",
            (_key(token), json.dumps({"expires": expires.isoformat()})),
        )
    return token


def invite_open(token: str) -> bool:
    with closing(store.connect()) as con:
        row = con.execute("SELECT value FROM meta WHERE key=?", (_key(token),)).fetchone()
    return _open(row["value"] if row else None)


def consume_invite(token: str) -> str | None:
    """Atomic, so two simultaneous submits cannot both create an account."""
    with closing(store.connect()) as con, con:
        row = con.execute(
            "DELETE FROM meta WHERE key=? RETURNING value", (_key(token),)
        ).fetchone()
    value = row["value"] if row else None
    return value if _open(value) else None


def _restore_invite(token: str, value: str) -> None:
    with closing(store.connect()) as con, con:
        con.execute("INSERT INTO meta(key,value) VALUES (?,?)", (_key(token), value))


def _public_base(request: Request) -> str:
    """What the admin's browser sees: the app may sit under a prefix behind a proxy."""
    proto = request.headers.get("x-forwarded-proto", "")
    scheme = proto if proto in ("http", "https") else request.url.scheme
    prefix = request.headers.get("x-forwarded-prefix", "")
    prefix = prefix.rstrip("/") if _PREFIX_RE.fullmatch(prefix) else ""
    return f"{scheme}://{request.url.netloc}{prefix}"


def _page(request: Request, token: str, *, error: str = "", username: str = "", name: str = "", status_code: int = 200):
    session_token, _ = _start_anonymous_session(request)
    response = templates.TemplateResponse(
        request,
        "invite.html",
        {"token": token, "error": error, "username": username, "name": name, "usable": invite_open(token), "min_length": MIN_PASSWORD_LENGTH},
        status_code=status_code,
    )
    if session_token:
        _set_session_cookie(response, session_token, ANONYMOUS_TTL_SECONDS)
    return response


@router.post("/users/uitnodiging", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def invite_create(request: Request):
    admin = require_admin(request)
    link = f"{_public_base(request)}/uitnodiging/{create_invite()}"
    log.info("invitation created by admin id=%s", admin["id"])
    return _users_response(request, user=admin, invite_link=link)


@router.get("/uitnodiging/{token}", response_class=HTMLResponse)
def invite_page(request: Request, token: str):
    if current_user(request) is not None:
        return RedirectResponse(url="/", status_code=303)
    return _page(request, token, status_code=200 if invite_open(token) else 404)


@router.post("/uitnodiging/{token}", response_class=HTMLResponse)
def invite_submit(
    request: Request,
    token: str,
    username: str = Form(""),
    display_name: str = Form(""),
    password: str = Form(""),
    csrf: str = Form("", alias=CSRF_FIELD),
):
    if current_user(request) is not None:
        return RedirectResponse(url="/", status_code=303)
    candidate, name = username.strip(), display_name.strip()[:80]
    session_id = getattr(request.state, "auth_session_id", None)
    session = _session_row(session_id) if session_id else None
    keep = {"username": candidate, "name": name}
    if session is None or not csrf or not secrets.compare_digest(csrf.encode(), session["csrf_token"].encode()):
        return _page(request, token, error="Je sessie is verlopen. Probeer het opnieuw.", status_code=403, **keep)
    if not invite_open(token):
        return _page(request, token, status_code=404)
    problem = ""
    if not _USERNAME_RE.fullmatch(candidate):
        problem = "Gebruik 3 tot 32 tekens: letters, cijfers, punt of streepje."
    elif len(password) < MIN_PASSWORD_LENGTH:
        problem = f"Kies een wachtwoord van minstens {MIN_PASSWORD_LENGTH} tekens."
    elif _get_user_by_username(candidate) is not None:
        problem = "Deze gebruikersnaam is al in gebruik."
    if problem:
        return _page(request, token, error=problem, status_code=400, **keep)
    invitation = consume_invite(token)
    if invitation is None:
        return _page(request, token, status_code=404)
    user_id = str(uuid.uuid4())
    try:
        _insert_user(user_id, candidate, name or candidate, ROLE_USER, password)
    except sqlite3.IntegrityError:  # Someone took this name between the check and now.
        _restore_invite(token, invitation)
        return _page(request, token, error="Deze gebruikersnaam is al in gebruik.", status_code=400, **keep)
    log.info("user created id=%s through an invitation", user_id)
    # They just chose this password: log them in instead of asking for it again.
    _revoke_session(session["token_hash"])
    token, _csrf = _create_session(user_id, SESSION_TTL_SECONDS, request.headers.get("user-agent"))
    _mark_login(user_id)
    activity.record(request, "invite_used", user_id=user_id)
    response = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(response, token, SESSION_TTL_SECONDS)
    return response
