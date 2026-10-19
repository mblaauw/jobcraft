"""Local accounts: username/password login, sessions and user administration.

Self-contained: this module only needs ``config.DB_PATH`` (via ``store.connect``),
``config.RUNTIME_DIR`` for the bootstrap credential file and the Jinja templates in
``config.TEMPLATES_DIR``. It never touches the LLM runtime, personal data or the
matching pipeline.

Public API used by ``app.main``::

    init_auth()          # create tables + bootstrap accounts (call in lifespan)
    middleware           # ASGI middleware for app.middleware("http")
    router               # APIRouter: /login /logout /account /users
    csrf_input(request)  # hidden CSRF field, register as Jinja global
    require_user(request), require_admin(request)  # route dependencies

Owned tables: ``users`` and ``auth_sessions`` (deliberately *not* ``sessions``,
which belongs to the interview/LLM store).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from base64 import b64decode, b64encode
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from . import config, store, tenancy
from .util import LOCAL_TZ, now_iso

log = logging.getLogger("jobcraft.auth")

COOKIE_NAME = "dca_session"
CSRF_FIELD = "_csrf"
CSRF_HEADER = "x-csrf-token"

SESSION_TTL_SECONDS = 14 * 24 * 3600
ANONYMOUS_TTL_SECONDS = 3600
LAST_SEEN_REFRESH_SECONDS = 60

ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLES = (ROLE_USER, ROLE_ADMIN)

LEGACY_USER_ID = "legacy"
LEGACY_DISPLAY_NAME = "Owner"
DEFAULT_USERNAME = "owner"
DEFAULT_PASSWORD = "dev-local"
BOOTSTRAP_FILE = "bootstrap-accounts.json"

MIN_PASSWORD_LENGTH = 10

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
PUBLIC_PATHS = frozenset({"/login", "/healthz"})

# Bootstrap accounts that exist so an admin can hand out access to a second person.
# Passwords are generated once and recorded in RUNTIME_DIR/bootstrap-accounts.json.
BOOTSTRAP_ACCOUNTS = (
    ("admin", ROLE_ADMIN, "Beheerder"),
    ("test-user", ROLE_USER, "Testgebruiker"),
)

_USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}", re.IGNORECASE)

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
"""

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
router = APIRouter()


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    """Salted scrypt hash, stdlib only: ``scrypt$n$r$p$salt$digest`` (base64)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    return "scrypt$16384$8$1${}${}".format(
        b64encode(salt).decode("ascii"), b64encode(digest).decode("ascii")
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not password or not stored:
        return False
    try:
        scheme, n_raw, r_raw, p_raw, salt_raw, digest_raw = str(stored).split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
    except (AttributeError, ValueError):
        return False
    # Bound the parameters we are willing to compute so a tampered row cannot stall us.
    if not (2**12 <= n <= 2**17) or not (1 <= r <= 16) or not (1 <= p <= 4):
        return False
    try:
        salt = b64decode(salt_raw, validate=True)
        expected = b64decode(digest_raw, validate=True)
    except (ValueError, TypeError):
        return False
    if not 8 <= len(salt) <= 64 or len(expected) < 16:
        return False
    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=128 * 1024 * 1024,
        )
    except (ValueError, OverflowError):
        return False
    return secrets.compare_digest(candidate, expected)


_dummy_hash: str | None = None
_bootstrap_credentials_lock = threading.Lock()


def _verify_dummy_password(password: str) -> None:
    """Spend comparable time when the username is unknown (no user enumeration)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(24))
    verify_password(password, _dummy_hash)


# ---------------------------------------------------------------- database


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _label_ts(value: str | None) -> str:
    parsed = _parse_ts(value)
    return parsed.astimezone(LOCAL_TZ).strftime("%d-%m-%Y %H:%M") if parsed else ""


def _expired(value: str | None) -> bool:
    parsed = _parse_ts(value)
    return parsed is None or parsed <= datetime.now(UTC)


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


# ---------------------------------------------------------------- sessions


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


# ---------------------------------------------------------------- csrf


def csrf_token(request: Request) -> str:
    return getattr(request.state, "csrf_token", "") or ""


def csrf_input(request: Request) -> Markup:
    """Hidden CSRF field for every unsafe form; registered as a Jinja global."""
    token = csrf_token(request)
    if not token:
        return Markup("")
    return Markup(f'<input type="hidden" name="{CSRF_FIELD}" value="{escape(token)}">')


async def _request_body(request: Request, limit: int = 256 * 1024) -> bytes | None:
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    if length > limit:
        return None
    try:
        body = await request.body()
    except Exception:  # noqa: BLE001 - an unreadable body can never carry a valid token
        return None
    return body if len(body) <= limit else None


async def _csrf_from_request(request: Request) -> str | None:
    """Accept the token from the X-CSRF-Token header or a ``_csrf`` form field."""
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type == "application/x-www-form-urlencoded":
        body = await _request_body(request)
        if body is None:
            return None
        values = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        candidates = values.get(CSRF_FIELD) or []
        return candidates[0] if candidates else None
    if content_type == "multipart/form-data":
        body = await _request_body(
            request, limit=6 * 1024 * 1024 if request.url.path == "/account/cv" else 256 * 1024
        )
        if body is None:
            return None
        match = re.search(rb'name="' + CSRF_FIELD.encode("ascii") + rb'"\r\n\r\n([^\r\n]*)', body)
        return match.group(1).decode("utf-8", "replace") if match else None
    return None


async def _csrf_matches(request: Request, session: dict | None) -> bool:
    if not session:
        return False
    expected = session.get("csrf_token") or ""
    provided = await _csrf_from_request(request)
    if not expected or not provided:
        return False
    return secrets.compare_digest(provided.encode(), expected.encode())


# ---------------------------------------------------------------- request guards


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def safe_next(value: str | None) -> str:
    """Return the value only when it is a local, non-login path; otherwise ``""``."""
    if not value:
        return ""
    target = str(value)
    if target != target.strip() or len(target) > 512:
        return ""
    if target.startswith("//") or "\\" in target or any(ord(char) < 32 for char in target):
        return ""
    try:
        parts = urlsplit(target)
    except ValueError:
        return ""
    if parts.scheme or parts.netloc or not parts.path.startswith("/"):
        return ""
    if parts.path in ("/login", "/logout"):
        return ""
    return target


def _wants_json(request: Request) -> bool:
    path = request.url.path
    return path == "/api" or path.startswith("/api/")


def _reject(request: Request, status_code: int, message: str, headers: dict | None = None):
    if _wants_json(request):
        return JSONResponse({"detail": message}, status_code=status_code, headers=headers)
    return Response(
        message, status_code=status_code, media_type="text/plain; charset=utf-8", headers=headers
    )


def _login_url(target: str) -> str:
    return "/login" + (f"?{urlencode({'next': target})}" if target else "")


def _login_redirect(request: Request) -> RedirectResponse:
    current = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(url=_login_url(safe_next(current)), status_code=303)


def _unauthenticated(request: Request) -> HTTPException:
    if _wants_json(request):
        return HTTPException(status_code=401, detail="Niet ingelogd.")
    current = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
    return HTTPException(status_code=303, headers={"Location": _login_url(safe_next(current))})


def require_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise _unauthenticated(request)
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user.get("role") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Alleen beheerders hebben toegang.")
    return user


# ---------------------------------------------------------------- tenancy scope


@contextmanager
def _scope(user: dict | None):
    with tenancy.scope(user):
        yield


# ---------------------------------------------------------------- middleware


def _uncached(response):
    # A second account on the same browser must not receive cached private pages
    # or credentials from a previous login (including one-time password screens).
    response.headers["Cache-Control"] = "no-store"
    return response


async def middleware(request: Request, call_next):
    """Resolve the session, publish request state and reject anonymous/unsafe calls."""
    session, user, invalid = _session_for_request(request)
    request.state.user = user
    request.state.csrf_token = (session or {}).get("csrf_token")
    request.state.auth_session_id = (session or {}).get("token_hash")
    request.state.anonymous_session = bool(session) and user is None

    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/static/"):
        response = await call_next(request)
        if invalid and not any(
            header.startswith(f"{COOKIE_NAME}=")
            for header in response.headers.getlist("set-cookie")
        ):
            _clear_session_cookie(response)
        return _uncached(response) if request.url.path == "/login" else response

    if user is None:
        response = (
            _reject(request, 401, "Niet ingelogd.")
            if _wants_json(request)
            else _login_redirect(request)
        )
        if invalid:
            _clear_session_cookie(response)
        return _uncached(response)

    if request.method not in SAFE_METHODS and not await _csrf_matches(request, session):
        return _uncached(_reject(request, 403, "CSRF-token ontbreekt of is ongeldig."))

    with _scope(user):
        return _uncached(await call_next(request))


# ---------------------------------------------------------------- login rate limit


_FAILURE_WINDOW_SECONDS = 900.0
_FAILURE_MAX = 8
_FAILURE_CAP = 2048

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _clock() -> float:
    return time.monotonic()


def _prune_failures(now: float) -> None:
    for key, stamps in list(_failures.items()):
        if not stamps or now - stamps[-1] > _FAILURE_WINDOW_SECONDS:
            _failures.pop(key, None)
    if len(_failures) > _FAILURE_CAP:
        ordered = sorted(_failures.items(), key=lambda item: item[1][-1])
        for key, _ in ordered[: len(_failures) - _FAILURE_CAP]:
            _failures.pop(key, None)


def _failure_key(request: Request, username: str) -> str:
    host = request.client.host if request.client else "?"
    return f"{host}|{username.strip().lower()}"


def _rate_limit_wait(key: str) -> float:
    """Seconds to wait before retrying, or 0. Window-based, so never a permanent lockout."""
    with _failures_lock:
        now = _clock()
        _prune_failures(now)
        stamps = [
            stamp for stamp in _failures.get(key, []) if now - stamp <= _FAILURE_WINDOW_SECONDS
        ]
        if len(stamps) < _FAILURE_MAX:
            return 0.0
        return max(0.0, _FAILURE_WINDOW_SECONDS - (now - min(stamps)))


def _record_failure(key: str) -> None:
    with _failures_lock:
        now = _clock()
        _prune_failures(now)
        _failures.setdefault(key, []).append(now)


def _clear_failures(key: str) -> None:
    with _failures_lock:
        _failures.pop(key, None)


# ---------------------------------------------------------------- views


def _login_response(
    request: Request,
    *,
    error: str = "",
    notice: str = "",
    username: str = "",
    next_url: str = "",
    status_code: int = 200,
    headers: dict | None = None,
):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "notice": notice, "username": username, "next_url": next_url},
        status_code=status_code,
        headers=headers,
    )


def _start_anonymous_session(request: Request) -> tuple[str | None, str]:
    """Reuse the anonymous session of this request, or start one. Never logs the token."""
    if getattr(request.state, "anonymous_session", False) and csrf_token(request):
        return None, csrf_token(request)
    token, csrf = _create_session(None, ANONYMOUS_TTL_SECONDS, request.headers.get("user-agent"))
    request.state.csrf_token = csrf
    request.state.anonymous_session = True
    return token, csrf


def _account_view(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": row["role"],
        "created_label": _label_ts(row["created_at"]) or "—",
        "last_login_label": _label_ts(row["last_login_at"]) or "—",
    }


def _account_response(request: Request, *, user: dict, error: str = "", status_code: int = 200):
    row = _get_user_by_id(user["id"])
    return templates.TemplateResponse(
        request,
        "account.html",
        {"account": _account_view(row) if row is not None else user, "error": error},
        status_code=status_code,
    )


def _user_admin_view(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": row["role"],
        "active": bool(row["active"]),
        "created_label": _label_ts(row["created_at"]) or "—",
        "last_login_label": _label_ts(row["last_login_at"]) or "—",
        "sessions": _session_count(row["id"]),
    }


def _users_response(
    request: Request,
    *,
    user: dict,
    error: str = "",
    notice: str = "",
    new_password: str = "",
    created_username: str = "",
    status_code: int = 200,
):
    rows = _list_users()
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "users": [_user_admin_view(row) for row in rows],
            "current_user_id": user["id"],
            "active_admins": sum(1 for row in rows if row["role"] == ROLE_ADMIN and row["active"]),
            "error": error,
            "notice": notice,
            "new_password": new_password,
            "created_username": created_username,
        },
        status_code=status_code,
    )


def _password_problem(new_password: str, confirm: str, current_hash: str | None) -> str:
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return f"Kies een wachtwoord van minstens {MIN_PASSWORD_LENGTH} tekens."
    if new_password != confirm:
        return "De twee wachtwoorden zijn niet gelijk."
    if verify_password(new_password, current_hash):
        return "Kies een wachtwoord dat je nog niet gebruikte."
    return ""


# ---------------------------------------------------------------- routes


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request) is not None:
        return RedirectResponse(
            url=safe_next(request.query_params.get("next")) or "/", status_code=303
        )
    target = safe_next(request.query_params.get("next"))
    token, _ = _start_anonymous_session(request)
    response = _login_response(request, next_url=target)
    if token:
        _set_session_cookie(response, token, ANONYMOUS_TTL_SECONDS)
    return response


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf: str = Form("", alias=CSRF_FIELD),
    next_url: str = Form("", alias="next"),
):
    session_id = getattr(request.state, "auth_session_id", None)
    session = _session_row(session_id) if session_id else None
    target = safe_next(next_url)
    if current_user(request) is not None:
        return RedirectResponse(url=target or "/", status_code=303)

    # CSRF first: credentials are only inspected with a token bound to this session.
    if (
        session is None
        or not csrf
        or not secrets.compare_digest(csrf.encode(), session["csrf_token"].encode())
    ):
        token, _ = _start_anonymous_session(request)
        response = _login_response(
            request,
            error="Je sessie is verlopen. Probeer het opnieuw.",
            username=username,
            next_url=target,
            status_code=403,
        )
        if token:
            _set_session_cookie(response, token, ANONYMOUS_TTL_SECONDS)
        return response

    key = _failure_key(request, username)
    wait = _rate_limit_wait(key)
    if wait:
        return _login_response(
            request,
            error="Te veel mislukte pogingen. Probeer het straks opnieuw.",
            username=username,
            next_url=target,
            status_code=429,
            headers={"Retry-After": str(int(wait) + 1)},
        )

    row = _get_user_by_username(username)
    if row is None:
        _verify_dummy_password(password)
    valid_password = verify_password(password, row["password_hash"]) if row is not None else False
    if row is None or not row["active"] or not valid_password:
        _record_failure(key)
        log.warning("login failed for user=%r", username)
        csrf = _rotate_csrf(session["token_hash"]) or session["csrf_token"]
        request.state.csrf_token = csrf
        return _login_response(
            request,
            error="Onjuiste gebruikersnaam of wachtwoord.",
            username=username,
            next_url=target,
            status_code=401,
        )

    _clear_failures(key)
    _revoke_session(session["token_hash"])
    token, csrf = _create_session(row["id"], SESSION_TTL_SECONDS, request.headers.get("user-agent"))
    _mark_login(row["id"])
    with _bootstrap_credentials_lock:
        accounts = _read_bootstrap_accounts()
        if isinstance(accounts.get(row["username"]), dict) and accounts[row["username"]].get(
            "password"
        ):
            accounts[row["username"]]["password"] = None
            _write_bootstrap_accounts(accounts)
    response = RedirectResponse(url=target or "/", status_code=303)
    _set_session_cookie(response, token, SESSION_TTL_SECONDS)
    log.info("login ok for user id=%s", row["id"])
    return response


@router.post("/logout")
def logout(request: Request):
    session_id = getattr(request.state, "auth_session_id", None)
    if session_id:
        _revoke_session(session_id)
    response = RedirectResponse(url="/login", status_code=303)
    _clear_session_cookie(response)
    return response


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    return _account_response(request, user=require_user(request))


@router.post("/account/password", response_class=HTMLResponse)
def account_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    user = require_user(request)
    row = _get_user_by_id(user["id"])
    if row is None:
        raise HTTPException(status_code=401, detail="Niet ingelogd.")
    if not verify_password(current_password, row["password_hash"]):
        return _account_response(
            request, user=user, error="Het huidige wachtwoord klopt niet.", status_code=400
        )
    problem = _password_problem(new_password, confirm_password, row["password_hash"])
    if problem:
        return _account_response(request, user=user, error=problem, status_code=400)
    _set_password(row["id"], new_password)
    revoked = _revoke_other_sessions(row["id"], getattr(request.state, "auth_session_id", None))
    log.info("password changed for user id=%s, %d other sessions revoked", row["id"], revoked)
    return RedirectResponse(url="/account?changed=1", status_code=303)


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    return _users_response(request, user=require_admin(request))


@router.post("/users", response_class=HTMLResponse)
def users_create(
    request: Request,
    username: str = Form(""),
    display_name: str = Form(""),
    role: str = Form(ROLE_USER),
    password: str = Form(""),
):
    admin = require_admin(request)
    candidate = username.strip()
    if not _USERNAME_RE.fullmatch(candidate):
        return _users_response(
            request,
            user=admin,
            error="Gebruik 3–32 tekens: letters, cijfers, punt, streepje of liggend streepje.",
            status_code=400,
        )
    if role not in ROLES:
        return _users_response(request, user=admin, error="Onbekende rol.", status_code=400)
    if _get_user_by_username(candidate) is not None:
        return _users_response(
            request, user=admin, error="Deze gebruikersnaam bestaat al.", status_code=400
        )

    generated = ""
    if password:
        if len(password) < MIN_PASSWORD_LENGTH:
            return _users_response(
                request,
                user=admin,
                error=f"Kies een wachtwoord van minstens {MIN_PASSWORD_LENGTH} tekens.",
                status_code=400,
            )
        chosen = password
    else:
        chosen = secrets.token_urlsafe(18)
        generated = chosen

    user_id = str(uuid.uuid4())
    _insert_user(user_id, candidate, display_name.strip() or candidate, role, chosen)
    log.info("user created id=%s role=%s by admin id=%s", user_id, role, admin["id"])
    return _users_response(
        request,
        user=admin,
        notice=f"Account {candidate} aangemaakt.",
        new_password=generated,
        created_username=candidate,
    )


@router.post("/users/{user_id}/deactivate", response_class=HTMLResponse)
def users_deactivate(request: Request, user_id: str):
    admin = require_admin(request)
    target = _get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Onbekende gebruiker.")
    if target["id"] == admin["id"]:
        return _users_response(
            request, user=admin, error="Je kunt je eigen account niet deactiveren.", status_code=400
        )
    outcome = _deactivate_without_losing_last_admin(target["id"])
    if outcome == "last_admin":
        return _users_response(
            request,
            user=admin,
            error="Er moet altijd minstens één actieve beheerder blijven.",
            status_code=400,
        )
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="Onbekende gebruiker.")
    revoked = _revoke_sessions_for(target["id"])
    log.info("user deactivated id=%s, %d sessions revoked", target["id"], revoked)
    return _users_response(request, user=admin, notice=f"{target['username']} is gedeactiveerd.")


@router.post("/users/{user_id}/activate", response_class=HTMLResponse)
def users_activate(request: Request, user_id: str):
    admin = require_admin(request)
    target = _get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Onbekende gebruiker.")
    _set_active(target["id"], True)
    log.info("user activated id=%s", target["id"])
    return _users_response(request, user=admin, notice=f"{target['username']} is weer actief.")


@router.post("/users/{user_id}/reset-password", response_class=HTMLResponse)
def users_reset_password(request: Request, user_id: str):
    admin = require_admin(request)
    target = _get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Onbekende gebruiker.")
    password = secrets.token_urlsafe(18)
    _set_password(target["id"], password)
    revoked = _revoke_sessions_for(target["id"])
    log.info("password reset for user id=%s, %d sessions revoked", target["id"], revoked)
    return _users_response(
        request,
        user=admin,
        notice=f"Nieuw wachtwoord voor {target['username']}. Alle sessies zijn afgemeld.",
        new_password=password,
        created_username=target["username"],
    )


# ---------------------------------------------------------------- bootstrap


def bootstrap_accounts_file() -> Path:
    return Path(config.RUNTIME_DIR) / BOOTSTRAP_FILE


def _read_bootstrap_accounts() -> dict:
    path = bootstrap_accounts_file()
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    accounts = data.get("accounts") if isinstance(data, dict) else None
    return accounts if isinstance(accounts, dict) else {}


def _write_bootstrap_accounts(accounts: dict) -> None:
    """Write the one-time generated passwords atomically with mode 0600."""
    path = bootstrap_accounts_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _read_bootstrap_accounts() == accounts:
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - best effort on exotic filesystems
            pass
        return
    payload = json.dumps({"accounts": accounts}, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _ensure_schema() -> None:
    with closing(store.connect()) as con, con:
        con.executescript(AUTH_SCHEMA)
        con.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?",
            (_iso(datetime.now(UTC) - timedelta(days=7)),),
        )


def init_auth() -> None:
    """Create the auth tables and the bootstrap accounts. Idempotent.

    Existing accounts keep their password and role: rerunning never resets a
    credential. Newly generated passwords are stored once in
    ``RUNTIME_DIR/bootstrap-accounts.json`` (mode 0600) and never logged.
    """
    legacy_username = (getattr(config, "APP_USER", "") or "").strip() or DEFAULT_USERNAME
    if legacy_username.casefold() in {name.casefold() for name, *_ in BOOTSTRAP_ACCOUNTS}:
        raise RuntimeError(
            "APP_USER is reserved for a new account; keep the existing account username."
        )
    _ensure_schema()
    accounts = _read_bootstrap_accounts()
    changed = False

    legacy_username = (getattr(config, "APP_USER", "") or "").strip() or DEFAULT_USERNAME
    legacy = _get_user_by_id(LEGACY_USER_ID)
    if legacy is None:
        _insert_user(
            LEGACY_USER_ID,
            legacy_username,
            LEGACY_DISPLAY_NAME,
            ROLE_USER,
            getattr(config, "APP_PASSWORD", "") or DEFAULT_PASSWORD,
        )
        log.info("auth bootstrap: local user %r created", legacy_username)
    elif legacy["username"] != legacy_username:
        # APP_USER moved: keep the login working without touching the password or role.
        if _get_user_by_username(legacy_username) is None:
            with closing(store.connect()) as con, con:
                con.execute(
                    "UPDATE users SET username=? WHERE id=?", (legacy_username, LEGACY_USER_ID)
                )
            log.info("auth bootstrap: local username now %r", legacy_username)

    for username, role, display_name in BOOTSTRAP_ACCOUNTS:
        existing = _get_user_by_username(username)
        if existing is None:
            password = secrets.token_urlsafe(18)
            user_id = str(uuid.uuid4())
            _insert_user(user_id, username, display_name, role, password)
            accounts[username] = {"id": user_id, "role": role, "password": password}
            changed = True
            log.info("auth bootstrap: account %r created", username)
        else:
            # Idempotent: never rewrite an existing password or role.
            entry = accounts.get(username)
            if not isinstance(entry, dict) or entry.get("id") != existing["id"]:
                accounts[username] = {
                    "id": existing["id"],
                    "role": existing["role"],
                    "password": None,
                }
                changed = True

    if changed or not bootstrap_accounts_file().exists():
        _write_bootstrap_accounts(accounts)


# The parent hands this function to Jinja so templates can render {{ csrf_input(request) }}.
templates.env.globals["csrf_input"] = csrf_input
