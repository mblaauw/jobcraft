"""Local authentication: security."""
from __future__ import annotations

import re
import secrets
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from markupsafe import Markup, escape

from .. import tenancy
from .constants import COOKIE_NAME, CSRF_FIELD, CSRF_HEADER, PUBLIC_PATHS, ROLE_ADMIN, SAFE_METHODS
from .sessions import _clear_session_cookie, _session_for_request


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



@contextmanager
def _scope(user: dict | None):
    with tenancy.scope(user):
        yield



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

