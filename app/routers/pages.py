"""routers pages responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)

from .. import (
    applications,
    auth,
    config,
    feedback,
    profile,
    sources,
    store,
    tasks,
    tenancy,
    today,
)
from ..web import templates

log = logging.getLogger("jobcraft")

WORKER_STALE_SECONDS = 180

router = APIRouter()


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(auth.require_user)])
def dashboard(request: Request):
    """Today: what to do to get closer to an interview, what is new, and how the week goes."""
    if auth.require_user(request)["role"] == auth.ROLE_ADMIN:
        return RedirectResponse(url="/admin", status_code=303)
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return RedirectResponse(url="/start", status_code=303)
    columns = applications.board()
    task_list = today.tasks(columns)
    matches, fresh = today.new_matches(5, today.last_visit())
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "start": today.start_state(),
            "first_name": today.first_name(),
            "has_profile": profile.profile_exists(),
            "tasks": task_list,
            "summary": today.summary(task_list, fresh),
            "matches": matches,
            "fresh": fresh,
            "week": today.week(columns),
            "tip": today.tip(),
            "has_jobs": bool(store.count_jobs()["total"]),
        },
    )


def _worker_state() -> dict:
    """In the combined role the worker runs inside this process, without its own heartbeat."""
    if os.environ.get("APP_ROLE", "all") != "web":
        return {"separate": False}
    raw = store.meta_get("worker_heartbeat")
    try:
        seen = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        seen = None
    alive = bool(seen and (datetime.now(UTC) - seen).total_seconds() < WORKER_STALE_SECONDS)
    return {"separate": True, "alive": alive, "seen": raw}


def _last_scan() -> str | None:
    try:
        health = json.loads(store.meta_get("source_health") or "{}")
    except ValueError:
        return None
    return max((item["last_ok"] for item in health.values() if item.get("last_ok")), default=None)


@router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(auth.require_admin)])
def admin_page(request: Request):
    auth.require_admin(request)
    votes = store.list_votes()
    source_rows = [dict(row) for row in store.list_sources()]
    overview = store.usage_overview()
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "preferences": profile.read_preferences(),
            "jobs": store.count_jobs(),
            "documents": store.count_documents(),
            "votes": {
                "up": len(votes["up"]),
                "down": len(votes["down"]),
            },
            "feedback": feedback.load(),
            "model": config.MODEL_LABEL,
            "profile_chars": len(profile.read_profile()),
            "sources": source_rows,
            "jobs_by_source": store.count_jobs_by_source(),
            "enabled_count": sum(1 for source in source_rows if source.get("enabled", True)),
            "answer_log_offset": store.meta_get("answer_log_offset", "0"),
            "bootstrap_status": store.meta_get("bootstrap_status", "idle"),
            "worker": _worker_state(),
            "overview": overview,
            # Many gateways report no price: then dollars say nothing and tokens are the brake.
            "priced": any(row["cost"] for row in overview["kinds"]),
            "token_limit": int(os.environ.get("AI_DAILY_TOKEN_LIMIT", "500000") or 0),
            "failures": tasks.failures_overview(),
            "last_scan": _last_scan(),
            "paths": {
                "data": str(config.DATA_DIR),
                "profile.md": str(config.user_path(profile.PROFILE_FILE)),
                "preferences.json": str(config.user_path(profile.PREFERENCES_FILE)),
                "feedback.json": str(config.user_path(feedback.FEEDBACK_FILE)),
                "sources.yaml": str(sources.DATA_FILE),
                "database": str(config.DB_PATH),
            },
        },
    )


@router.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})
