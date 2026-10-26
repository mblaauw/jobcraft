"""routers sources responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
)

from .. import (
    auth,
    sources,
    store,
    tasks,
)
from ..auth import require_user
from ..web import templates

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    summary = None
    raw = store.meta_get("scan_summary")
    if raw:
        try:
            summary = json.loads(raw)
        except ValueError:
            summary = None
    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "sources": store.list_sources(),
            "user_sources": store.effective_user_sources(),
            "jobs_by_source": store.count_jobs_by_source(),
            "scan_status": store.meta_get("scan_status", "idle"),
            "summary": summary,
            "counts": store.count_jobs(),
            "schedule_enabled": store.meta_get("scan_schedule_enabled", "1") == "1",
            "schedule_times": store.meta_get(
                "scan_schedule_times", os.environ.get("SCAN_TIMES", "07:30,17:30")
            ),
            "schedule_last_fire": store.meta_get("scheduler_last_fire"),
        },
    )


@router.post("/sources/{source_id}/toggle", dependencies=[Depends(auth.require_admin)])
def source_toggle(request: Request, source_id: str):
    auth.require_admin(request)
    known = {source["id"]: source for source in sources.load_sources()}
    source = known.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="unknown source")
    current = bool(source.get("enabled", True))
    if not sources.set_enabled(source_id, not current):
        raise HTTPException(status_code=500, detail="could not update sources.yaml")
    return RedirectResponse(url="/sources", status_code=303)


@router.post("/sources/scan", dependencies=[Depends(auth.require_admin)])
def sources_scan(request: Request):
    auth.require_admin(request)
    tasks.enqueue("scan", owner=None, dedupe_key="scan")
    return RedirectResponse(url="/sources", status_code=303)


@router.post("/sources/schedule", dependencies=[Depends(auth.require_admin)])
def sources_schedule(request: Request, enabled: str = Form("1"), times: str = Form("07:30,17:30")):
    auth.require_admin(request)
    store.meta_set("scan_schedule_enabled", "1" if enabled == "1" else "0")
    store.meta_set("scan_schedule_times", times.strip() or "07:30,17:30")
    return RedirectResponse(url="/sources", status_code=303)


@router.post("/sources/{source_id}/preference")
def source_preference(source_id: str, enabled: str = Form("1")):
    known = {source["id"] for source in sources.load_sources()}
    if source_id not in known:
        raise HTTPException(status_code=404, detail="unknown source")
    store.set_user_source(source_id, enabled == "1")
    return RedirectResponse(url="/sources", status_code=303)
