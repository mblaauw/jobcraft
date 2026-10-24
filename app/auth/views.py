"""Local authentication: views."""

from __future__ import annotations

from fastapi import Request

from .. import config
from ..web import templates
from .constants import ANONYMOUS_TTL_SECONDS, MIN_PASSWORD_LENGTH, ROLE_ADMIN
from .credentials import verify_password
from .security import csrf_token
from .sessions import _create_session
from .timestamps import _label_ts
from .users import _get_user_by_id, _list_users, _session_count


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
        {
            "account": _account_view(row) if row is not None else user,
            "error": error,
            "has_cv": (config.seed_dir() / config.seed_files()["cv"]).is_file(),
        },
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
