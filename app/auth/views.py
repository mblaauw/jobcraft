"""Local authentication: views."""

from __future__ import annotations

import os
import sqlite3

from fastapi import Request

from .. import config, profile, store
from ..web import templates
from .activity import browser_label
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


def _cv_generated() -> bool:
    try:
        return store.meta_get("cv_generated") == "1"
    except sqlite3.OperationalError:  # The auth tables can exist before the app tables.
        return False


def _digest_view() -> dict:
    view = {
        "smtp": bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM")),
        "email": "",
        "enabled": False,
    }
    try:
        view["email"] = store.meta_get("digest_email") or ""
        view["enabled"] = store.meta_get("digest_enabled") == "1"
    except sqlite3.OperationalError:  # Sign-in works before the rest of the schema exists.
        pass
    return view


def _account_response(request: Request, *, user: dict, error: str = "", status_code: int = 200):
    row = _get_user_by_id(user["id"])
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "account": _account_view(row) if row is not None else user,
            "seeker": profile.seeker(user),
            "devices": _session_count(user["id"]),
            "error": error,
            "has_cv": (config.seed_dir() / config.seed_files()["cv"]).is_file(),
            "cv_generated": _cv_generated(),
            "digest": _digest_view(),
        },
        status_code=status_code,
    )


ACTIVITY_LABELS = {
    "login_ok": "Ingelogd",
    "login_failed": "Inloggen mislukt",
    "logout": "Uitgelogd",
    "invite_used": "Account gemaakt via uitnodiging",
    "password_changed": "Wachtwoord gewijzigd",
    "password_reset": "Wachtwoord opnieuw ingesteld door beheerder",
    "deactivated": "Gedeactiveerd door beheerder",
    "activated": "Geactiveerd door beheerder",
    "sessions_revoked": "Overal anders uitgelogd",
}
ACTIVITY_PERIODS = {"dag": 1, "week": 7, "maand": 31}


def _user_admin_view(row, last: dict | None = None, failed: int = 0) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": row["role"],
        "active": bool(row["active"]),
        "created_label": _label_ts(row["created_at"]) or "—",
        "last_login_label": _label_ts(row["last_login_at"]) or "—",
        "last_ip": (last or {}).get("ip") or "",
        "last_browser": browser_label((last or {}).get("user_agent")) if last else "",
        "failed_today": failed,
        "sessions": _session_count(row["id"]),
    }


def _activity_response(request: Request, *, account: str, kind: str, period: str):
    """Sign-in activity for administrators: account-level only, never what people did."""
    days = ACTIVITY_PERIODS.get(period, 7)
    period = period if period in ACTIVITY_PERIODS else "week"
    kind = kind if kind in ACTIVITY_LABELS else ""
    users = _list_users()
    names = {row["id"]: (row["display_name"] or row["username"]) for row in users}
    account = account if account in names else ""
    events = [
        {
            **event,
            "label": ACTIVITY_LABELS.get(event["kind"], event["kind"]),
            "when": _label_ts(event["at"]) or event["at"],
            "name": names.get(event["user_id"], "") if event["user_id"] else "",
            "actor": names.get(event["actor_id"], "") if event["actor_id"] else "",
            "browser": browser_label(event["user_agent"]),
            "failed": event["kind"] == "login_failed",
        }
        for event in store.list_auth_events(user_id=account or None, kind=kind or None, days=days)
    ]
    guessers = [
        {**row, "last": _label_ts(row["last"]) or row["last"]} for row in store.failed_attempts(days=1)
    ]
    cut_off = len(events) >= store.auth_events.PAGE_LIMIT
    return templates.TemplateResponse(
        request,
        "users_activity.html",
        {
            "events": events,
            "guessers": guessers,
            "names": names,
            "labels": ACTIVITY_LABELS,
            "periods": ACTIVITY_PERIODS,
            "filter_account": account,
            "filter_kind": kind,
            "filter_period": period,
            "cut_off": cut_off,
            "retention_days": store.auth_events.retention_days(),
        },
    )


def _users_response(
    request: Request,
    *,
    user: dict,
    error: str = "",
    notice: str = "",
    new_password: str = "",
    created_username: str = "",
    invite_link: str = "",
    status_code: int = 200,
):
    rows = _list_users()
    last, failed = store.last_logins(), store.failed_per_user(days=1)
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "users": [_user_admin_view(row, last.get(row["id"]), failed.get(row["id"], 0)) for row in rows],
            "current_user_id": user["id"],
            "active_admins": sum(1 for row in rows if row["role"] == ROLE_ADMIN and row["active"]),
            "error": error,
            "notice": notice,
            "new_password": new_password,
            "created_username": created_username,
            "invite_link": invite_link,
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
