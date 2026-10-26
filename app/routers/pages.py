"""routers pages responsibilities extracted without route changes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
)

from .. import (
    auth,
    config,
    feedback,
    matching,
    profile,
    ranking,
    sources,
    store,
)
from ..web import templates
from ..web_helpers import _profile_meta, _workflow

log = logging.getLogger("jobcraft")

router = APIRouter()


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(auth.require_user)])
def dashboard(request: Request):
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
    for row in store.list_jobs(limit * 8, "new"):
        if row["vote"]:
            continue
        item = dict(row)
        item["travel"] = ranking.travel_of(item)
        if item["travel"]["in_range"] is False:
            continue
        item["signals"] = ranking.signals_of(row)
        picks.append(item)
        if len(picks) == limit:
            break
    return picks


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
            "usage": store.usage_total(),
            "model": config.MODEL_LABEL,
            "profile_chars": len(profile.read_profile()),
            "sources": source_rows,
            "jobs_by_source": store.count_jobs_by_source(),
            "enabled_count": sum(1 for source in source_rows if source.get("enabled", True)),
            "answer_log_offset": store.meta_get("answer_log_offset", "0"),
            "bootstrap_status": store.meta_get("bootstrap_status", "idle"),
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

