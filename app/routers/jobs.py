"""routers jobs responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
)

from .. import (
    config,
    feedback,
    geo,
    matching,
    profile,
    ranking,
    store,
    systemone,
    tasks,
    tenancy,
)
from ..auth import require_user
from ..runtime import get_manager
from ..web import templates
from ..web_helpers import _jobs_return_target
from ..workflows import _doc_status, _document_lock

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    status: str = "",
    q: str = "",
    source: str = "",
    within: str = "",
    stale: str = "",
    page: int = 1,
    per_page: int = 50,
    sort: str = "rank",
):
    sort = sort if sort in ("rank", "newest") else "rank"
    stale_active = stale == "1"
    raw = store.meta_get("match_summary")
    summary = None
    if raw:
        try:
            summary = json.loads(raw)
        except ValueError:
            summary = None
    rows = store.list_jobs(
        None, status or None, q or None, source or None, sort=sort, include_stale=stale_active
    )
    source_rows = store.list_sources()
    source_names = {row["id"]: row["name"] for row in source_rows}
    jobs_view = []
    for row in rows:
        item = dict(row)
        date = store.job_date(row)
        item["display_date"] = date.strftime("%d-%m-%Y") if date.year > 1 else ""
        item["date_iso"] = date.date().isoformat() if date.year > 1 else ""
        item["source_name"] = source_names.get(
            item["source_id"], item["source_id"] or "Onbekende bron"
        )
        item["travel"] = ranking.travel_of(item)
        item["signals"] = ranking.signals_of(row)
        jobs_view.append(item)
    within_active = within != "0"  # reisbereik-filter staat standaard aan
    excluded = 0
    if within_active:
        excluded = sum(1 for item in jobs_view if item["travel"]["in_range"] is False)
        jobs_view = [item for item in jobs_view if item["travel"]["in_range"] is not False]
    total_filtered = len(jobs_view)
    per_page = max(10, min(200, per_page))
    pages = max(1, (total_filtered + per_page - 1) // per_page)
    page = max(1, min(pages, page))
    start = (page - 1) * per_page
    page_items = jobs_view[start : start + per_page]

    params = {
        "status": status,
        "q": q,
        "source": source,
        "within": "1" if within_active else "0",
        "stale": "1" if stale_active else "",
        "sort": sort,
        "per_page": per_page,
    }
    pager_base = "/jobs?" + urlencode(params)
    status_counts = store.status_counts(
        source=source or None, query_text=q or None, include_stale=stale_active
    )
    total_status = sum(status_counts.values())
    status_count_for = {
        "": total_status,
        "new": status_counts.get("new", 0),
        "saved": status_counts.get("saved", 0),
        "applied": status_counts.get("applied", 0),
        "ignored": status_counts.get("ignored", 0),
    }
    status_links = [
        {
            "label": label,
            "value": value,
            "count": status_count_for[value],
            "url": "/jobs?" + urlencode({**params, "status": value}),
        }
        for value, label in (
            ("", "Alle"),
            ("new", "Nieuw"),
            ("saved", "Bewaard"),
            ("applied", "Gereageerd"),
            ("ignored", "Genegeerd"),
        )
    ]
    filter_chips = []
    for key, label in (
        ("q", f"Zoekterm: {q}"),
        ("source", f"Bron: {source_names.get(source, source)}"),
    ):
        if params[key]:
            filter_chips.append({"label": label, "url": "/jobs?" + urlencode({**params, key: ""})})
    if not within_active:
        filter_chips.append(
            {
                "label": "Ook buiten reisbereik",
                "url": "/jobs?" + urlencode({**params, "within": "1"}),
            }
        )
    if stale_active:
        filter_chips.append(
            {
                "label": "Verlopen vacatures",
                "url": "/jobs?" + urlencode({**params, "stale": ""}),
            }
        )

    return_to = str(request.url.path)
    if request.url.query:
        return_to += f"?{request.url.query}"
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": page_items,
            "counts": store.count_jobs(),
            "status_filter": status,
            "query": q,
            "source_filter": source,
            "within": "1" if within_active else "0",
            "stale": "1" if stale_active else "0",
            "stale_active": stale_active,
            "stale_url": "/jobs?" + urlencode({**params, "stale": "1"}),
            "status_links": status_links,
            "filter_chips": filter_chips,
            "reset_url": "/jobs?"
            + urlencode({"status": status, "sort": sort, "per_page": per_page}),
            "sort": sort,
            "excluded": excluded,
            "page": page,
            "pages": pages,
            "per_page": per_page,
            "total_filtered": total_filtered,
            "pager_base": pager_base,
            "sources": source_rows,
            "scan_status": store.meta_get("scan_status", "idle"),
            "match_status": store.meta_get("match_status", "idle"),
            "match_summary": summary,
            "feedback": feedback.load(),
            "geo_home": geo.home(),
            "queue": ranking.queue_depth(matching.current_digest()),
            "systemone_enabled": systemone.enabled(),
            "return_to": return_to,
        },
    )


@router.post("/jobs/match")
def jobs_match_route(url: str = Form("/jobs", alias="next")):
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return RedirectResponse(url="/interview", status_code=303)
    get_manager()
    owner = tenancy.user_id()
    tasks.enqueue("match", owner=owner, dedupe_key=f"match:{owner}")
    return RedirectResponse(url=_jobs_return_target(url), status_code=303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, return_to: str = ""):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    reason = None
    if job["match_reason"]:
        try:
            reason = json.loads(job["match_reason"])
        except ValueError:
            reason = {"why": job["match_reason"]}
    doc_status = _doc_status(job_id)
    doc_status_recent = False
    if doc_status.get("state") == "done" and doc_status.get("finished_at"):
        try:
            finished = datetime.fromisoformat(doc_status["finished_at"])
            doc_status_recent = (datetime.now(UTC) - finished).total_seconds() < 600
        except (TypeError, ValueError):
            doc_status_recent = False
    item = dict(job)
    travel = geo.assess(
        item.get("location"),
        item.get("lat"),
        item.get("lon"),
        description=item.get("description"),
        title=item.get("title"),
    )
    signals = ranking.signals_of(job)
    list_return = _jobs_return_target(return_to)
    if urlsplit(list_return).path != "/jobs":
        list_return = "/jobs"
    detail_return = f"/jobs/{job_id}?" + urlencode({"return_to": list_return})
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": item,
            "reason": reason,
            "signals": signals,
            "counts": store.count_jobs(),
            "documents": store.list_documents(job_id),
            "doc_error": request.query_params.get("doc_error", ""),
            "doc_status": doc_status,
            "doc_status_recent": doc_status_recent,
            "travel": travel,
            "list_return": list_return,
            "detail_return": detail_return,
        },
    )


@router.post("/jobs/{job_id}/documents")
def job_document(job_id: int, kind: str = Form(...), url: str = Form("", alias="next")):
    if tenancy.user_id() != tenancy.LEGACY_ID and (
        not profile.profile_exists() or not (config.seed_dir() / "cv.docx").exists()
    ):
        raise HTTPException(
            status_code=409,
            detail="Vul eerst je profiel aan en upload je eigen basis-CV via Account.",
        )
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    get_manager()
    if kind not in ("resume", "motivatie"):
        raise HTTPException(status_code=400, detail="unknown document kind")
    target = _jobs_return_target(url)
    if urlsplit(target).path != f"/jobs/{job_id}":
        target = f"/jobs/{job_id}"
    target = urlsplit(target)._replace(fragment="documenten").geturl()
    with _document_lock:
        if _doc_status(job_id).get("state") == "running":
            return RedirectResponse(url=target, status_code=303)
        store.meta_set(
            f"doc_status:{job_id}",
            json.dumps(
                {"state": "running", "kind": kind, "started_at": datetime.now(UTC).isoformat()}
            ),
        )
    owner = tenancy.user_id()
    tasks.enqueue(
        "document",
        owner=owner,
        payload={"job_id": job_id, "kind": kind},
        dedupe_key=f"document:{owner}:{job_id}:{kind}",
    )
    return RedirectResponse(url=target, status_code=303)


@router.get("/jobs/{job_id}/documents/{document_id}/{fmt}")
def download_document(job_id: int, document_id: int, fmt: str, inline: int = 0):
    document = store.get_document(document_id)
    if not document or document["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="unknown document")
    relative = document["pdf_path"] if fmt == "pdf" else document["docx_path"]
    if not relative:
        raise HTTPException(status_code=404, detail="format not available")
    path = (config.DATA_DIR / relative).resolve()
    if not path.is_file() or not path.is_relative_to(config.jobs_dir().resolve()):
        raise HTTPException(status_code=404, detail="file missing")
    media = (
        "application/pdf"
        if fmt == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(
        path,
        media_type=media,
        filename=path.name,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.post("/jobs/{job_id}/vote")
def job_vote(job_id: int, vote: str = Form(...), url: str = Form("/jobs", alias="next")):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    value = {"up": 1, "down": -1, "clear": 0}.get(vote)
    if value is None:
        raise HTTPException(status_code=400, detail="unknown vote")
    store.set_job_vote(job_id, value)
    feedback.rebuild()
    ranking.rerank_one(job_id)
    owner = tenancy.user_id()
    tasks.enqueue("rerank", owner=owner, dedupe_key=f"rerank:{owner}")
    target = _jobs_return_target(url)
    return RedirectResponse(url=f"{target}", status_code=303)


@router.post("/jobs/{job_id}/status")
def job_status(job_id: int, status: str = Form(...), url: str = Form("/jobs", alias="next")):
    if status not in ("new", "saved", "ignored", "applied"):
        raise HTTPException(status_code=400, detail="unknown status")
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="unknown job")
    store.update_job_status(job_id, status)
    return RedirectResponse(url=_jobs_return_target(url), status_code=303)
