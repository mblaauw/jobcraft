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
    matching,
    profile,
    ranking,
    sources,
    store,
    tasks,
    tenancy,
)
from ..web import templates
from ..web_helpers import _profile_meta, _workflow

log = logging.getLogger("jobcraft")

WORKER_STALE_SECONDS = 180

router = APIRouter()


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(auth.require_user)])
def dashboard(request: Request):
    if auth.require_user(request)["role"] == auth.ROLE_ADMIN:
        return RedirectResponse(url="/admin", status_code=303)
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return RedirectResponse(url="/start", status_code=303)
    sessions = store.list_sessions(8)
    profile_state = _profile_meta()
    jobs = store.count_jobs()
    documents = store.count_documents()
    draft_pair = profile.pending_draft()
    draft = draft_pair[1] if draft_pair else None
    queue = ranking.queue_depth(matching.current_digest())
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "top_jobs": _top_new_jobs(5),
            "reminders": applications.reminders(),
            "pipeline": {
                status: len(store.list_jobs(None, status)) for status in ("saved", "applied")
            },
            "sessions": sessions,
            "profile_state": profile_state,
            "seed": profile.seed_status(),
            "usage": store.usage_total(),
            "bootstrap_status": store.meta_get("bootstrap_status", "idle"),
            "jobs": jobs,
            "documents": documents,
            "draft": draft,
            "queue": queue,
            "next_step": _workflow(profile_state, jobs, documents, draft, queue),
            "model": config.MODEL_LABEL,
        },
    )


def _top_new_jobs(limit: int) -> list[dict]:
    """Best-ranked new vacancies the user has not voted on and can plausibly reach."""
    picks: list[dict] = []
    for row in store.list_jobs(None, "new", only_unvoted=True):
        item = dict(row)
        item["travel"] = ranking.travel_of(item)
        if item["travel"]["in_range"] is False:
            continue
        item["signals"] = ranking.signals_of(row)
        picks.append(item)
        if len(picks) == limit:
            break
    return picks


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
            "overview": store.usage_overview(),
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
