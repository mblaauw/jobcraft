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
    application_plan,
    applications,
    config,
    document_edits,
    feedback,
    geo,
    insights,
    matching,
    notifications,
    profile,
    ranking,
    store,
    tasks,
    tenancy,
    vacancy_reader,
)
from ..auth import require_user
from ..runtime import get_manager
from ..scoring import reasons
from ..util import LOCAL_TZ
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
    remote: str = "",
    contract: str = "",
    salaris: str = "",
    dagen: str = "",
    uren: str = "",
):
    sort = sort if sort in ("rank", "newest") else "rank"
    remote = "remote" if remote == "remote" else ""
    contract = contract if contract in ("vast", "tijdelijk", "freelance") else ""
    salaris = "salaris" if salaris == "salaris" else ""
    dagen = dagen if dagen in ("1", "7", "30") else ""
    uren = uren if uren in ("tot24", "24-32", "32-40") else ""
    list_filters = {
        "remote": remote,
        "contract": contract,
        "salaris": salaris,
        "days": dagen,
        "hours": uren,
    }
    stale_active = stale == "1"
    raw = store.meta_get("match_summary")
    summary = None
    if raw:
        try:
            summary = json.loads(raw)
        except ValueError:
            summary = None
    ranking.refresh_travel()
    source_rows = store.list_sources()
    source_names = {row["id"]: row["name"] for row in source_rows}
    within_active = within != "0"  # reisbereik-filter staat standaard aan
    per_status = store.job_status_counts(
        q or None, source or None, include_stale=stale_active, list_filters=list_filters
    )
    status_totals = {
        key: all_jobs - far if within_active else all_jobs
        for key, (all_jobs, far) in per_status.items()
    }
    excluded_by_status = (
        {key: far for key, (_, far) in per_status.items() if far} if within_active else {}
    )
    excluded = excluded_by_status.get(status, 0) if status else sum(excluded_by_status.values())
    total_filtered = status_totals.get(status, 0) if status else sum(status_totals.values())
    per_page = max(10, min(200, per_page))
    pages = max(1, (total_filtered + per_page - 1) // per_page)
    page = max(1, min(pages, page))
    page_items = []
    preferences = profile.read_preferences()
    for row in store.list_jobs_page(
        per_page,
        (page - 1) * per_page,
        status or None,
        q or None,
        source or None,
        sort=sort,
        include_stale=stale_active,
        within_travel=within_active,
        list_filters=list_filters,
    ):
        item = dict(row)
        date = store.job_date(item)
        item["display_date"] = (
            date.astimezone(LOCAL_TZ).strftime("%d-%m-%Y") if date.year > 1 else ""
        )
        item["date_iso"] = date.date().isoformat() if date.year > 1 else ""
        item["source_name"] = (
            "Zelf toegevoegd"
            if (item["source_id"] or "").startswith("import:")
            else source_names.get(item["source_id"], item["source_id"] or "Onbekende bron")
        )
        item["travel"] = ranking.travel_of(item)
        item["signals"] = ranking.signals_of(item)
        item["reason_line"] = reasons.reason_line(item, preferences)
        item["facts"] = reasons.facts(item)
        page_items.append(item)

    params = {
        "status": status,
        "q": q,
        "source": source,
        "within": "1" if within_active else "0",
        "stale": "1" if stale_active else "",
        "sort": sort,
        "per_page": per_page,
        "remote": remote,
        "contract": contract,
        "salaris": salaris,
        "dagen": dagen,
        "uren": uren,
    }
    pager_base = "/jobs?" + urlencode(params)
    total_status = sum(status_totals.values())
    status_count_for = {
        "": total_status,
        "new": status_totals.get("new", 0),
        "saved": status_totals.get("saved", 0),
        "applied": status_totals.get("applied", 0),
        "ignored": status_totals.get("ignored", 0),
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
        ("remote", "Thuiswerken genoemd"),
        ("contract", f"Contract: {contract}"),
        ("salaris", "Salaris genoemd"),
        ("dagen", f"Laatste {dagen} dagen"),
        ("uren", f"Uren: {uren}"),
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
    geo_home = geo.home()
    geo_home["configured"] = bool(
        (geo_home.get("lat") or geo_home.get("lon"))
        and geo.valid_coordinates(geo_home.get("lat") or 0, geo_home.get("lon") or 0)
    )
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
            "list_filters": params,
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
            "geo_home": geo_home,
            "queue": ranking.queue_depth(matching.current_digest()),
            "return_to": return_to,
            "welcome": request.query_params.get("welkom") == "1",
            "new_match_total": notifications.badge_count(),
        },
    )


@router.post("/jobs/match")
def jobs_match_route(url: str = Form("/jobs", alias="next")):
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return RedirectResponse(url="/start", status_code=303)
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
    store.count_event("jobs_opened")
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
    vacancy_reader.request(item)
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
            "has_plan": application_plan.load(job_id) is not None,
            "travel": travel,
            "list_return": list_return,
            "detail_return": detail_return,
            "facts": reasons.facts(item),
            "vacancy": vacancy_reader.view(item),
            **document_edits.detail_context(store.list_documents(job_id)),
            **insights.context(job, request.query_params.get("inzicht_fout", "")),
        },
    )


@router.post("/jobs/{job_id}/documents")
def job_document(job_id: int, kind: str = Form(...), url: str = Form("", alias="next")):
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        raise HTTPException(status_code=409, detail="Vul eerst je profiel aan.")
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
    store.count_event("documents_requested")
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
    ranking.after_vote(job_id)
    owner = tenancy.user_id()
    tasks.enqueue("rerank", owner=owner, dedupe_key=f"rerank:{owner}")
    target = _jobs_return_target(url)
    return RedirectResponse(url=f"{target}", status_code=303)


@router.post("/jobs/{job_id}/status")
def job_status(job_id: int, status: str = Form(...), url: str = Form("/jobs", alias="next")):
    if status not in ("new", "ignored", *applications.STATUSES):
        raise HTTPException(status_code=400, detail="unknown status")
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="unknown job")
    store.update_job_status(job_id, status)
    ranking.after_vote(job_id)  # saved and ignored teach the ranking as much as a vote
    if status == "applied":
        store.count_event("applications")
    return RedirectResponse(url=_jobs_return_target(url), status_code=303)
