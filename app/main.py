"""Jobcraft — FastAPI app and routes."""

from __future__ import annotations

import difflib
import io
import json
import logging
import os
import threading
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import (
    auth,
    config,
    docs,
    feedback,
    geo,
    interview_state,
    llm,
    matching,
    profile,
    ranking,
    scheduler,
    sources,
    store,
    systemone,
    tasks,
    tenancy,
)
from .util import now_local

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("jobcraft")

manager: llm.ServerManager | None = None
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global manager
    store.init_db()
    if store.load_token_df() is None:
        feedback.refresh_document_frequency()
    auth.init_auth()
    profile.ensure_dirs()
    interview_state.ensure_state()
    sources.ensure_sources_file()
    store.sync_sources(sources.load_sources())
    try:
        if not docs.CV_TEMPLATE.exists():
            docs.ensure_templates()
    except Exception as exc:  # noqa: BLE001 - documents stay disabled without the base CV
        log.warning("document templates unavailable: %s", exc)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    store.recover_interrupted_work()
    tasks.recover_stale()
    tasks.start_workers(2)
    scheduler.start()
    if not config.APP_SKIP_LLM:
        manager = llm.ServerManager()
        manager.start()
        if manager.client is None:
            log.warning("LLM features disabled: set LLM_BASE_URL and LLM_API_KEY")
        else:
            log.info("LLM client ready for %s", manager.base_url)
    yield
    scheduler.stop()
    tasks.stop_workers()
    if manager:
        manager.stop()


app = FastAPI(title="Jobcraft", lifespan=lifespan)
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.globals["csrf_input"] = auth.csrf_input
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
app.include_router(auth.router)


# ---------------------------------------------------------------- auth


app.middleware("http")(auth.middleware)


def get_manager() -> llm.ServerManager:
    if manager is None or manager.client is None:
        raise HTTPException(
            status_code=503, detail="LLM is niet geconfigureerd (LLM_BASE_URL/LLM_API_KEY)."
        )
    return manager


def _session_lock(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        return _session_locks.setdefault(session_id, threading.Lock())


# ---------------------------------------------------------------- helpers


def _model_label(model) -> str | None:
    if isinstance(model, dict):
        return f"{model.get('providerID')}/{model.get('id')}"
    return str(model) if model else None


def _persist_turn(session_id: str, result: dict, kind: str, *, hide_kickoff: bool = False) -> None:
    for message in result["messages"]:
        message_id = message.get("id") or f"anon-{uuid.uuid4()}"
        hidden = int(
            hide_kickoff
            and message["role"] == "user"
            and message["text"].strip() == config.KICKOFF_TEXT
        )
        store.add_message(
            session_id,
            message_id,
            message["role"],
            message["text"],
            _model_label(message.get("model")),
            hidden,
        )
    usage = result.get("usage") or {}
    if usage.get("cost_usd") or usage.get("input_tokens") or usage.get("output_tokens"):
        store.record_usage(
            session_id,
            kind,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            float(usage.get("cost_usd") or 0.0),
        )
    session = result.get("session") or {}
    if session:
        store.update_session(
            session_id,
            cost_usd=float(session.get("cost") or 0.0),
            tokens_in=int((session.get("tokens") or {}).get("input") or 0),
            tokens_out=int((session.get("tokens") or {}).get("output") or 0),
        )
    else:
        store.add_session_usage(
            session_id,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            float(usage.get("cost_usd") or 0.0),
        )


def _last_assistant_text(session_id: str) -> str:
    for row in reversed(store.list_messages(session_id)):
        if row["role"] == "assistant" and row["text"].strip():
            return row["text"].strip()
    return ""


def _visible_messages(session_id: str) -> list[dict]:
    rows = store.list_messages(session_id)
    visible = []
    for row in rows:
        if row["hidden"]:
            continue
        visible.append({"role": row["role"], "text": row["text"]})
    return visible


def _profile_meta() -> dict:
    exists = profile.profile_exists()
    updated = None
    if exists:
        updated = datetime.fromtimestamp(
            config.user_path(profile.PROFILE_FILE).stat().st_mtime, UTC
        ).isoformat()
    return {"exists": exists, "updated": updated}


def _workflow(
    profile_state: dict, jobs: dict, documents: int, draft: dict | None, queue: int
) -> dict:
    if not profile_state["exists"]:
        if tenancy.user_id() != tenancy.LEGACY_ID:
            return {
                "label": "Vertel wat je zoekt",
                "href": "/interview",
                "detail": "Start een gesprek om je eigen profiel op te bouwen.",
                "step": 1,
            }
        return {
            "label": "Profiel bootstrappen",
            "href": "/profile",
            "detail": "Lees de bestaande materialen in en maak de canon.",
            "step": 1,
        }
    if draft:
        return {
            "label": "Consolidatievoorstel beoordelen",
            "href": "/profile",
            "detail": "Er wacht een profielvoorstel — vergelijk en accepteer of verwerp.",
            "step": 1,
        }
    if jobs["total"] == 0:
        return {
            "label": "Vacaturebronnen scannen",
            "href": "/sources",
            "detail": "Haal de nieuwste vacatures op bij de bronnen.",
            "step": 2,
        }
    if queue > 0:
        return {
            "label": f"Vacatures matchen ({queue} in wachtrij)",
            "href": "/jobs",
            "detail": "Alleen de kansrijke, onzekere vacatures gaan naar de matcher.",
            "step": 3,
        }
    if documents == 0:
        return {
            "label": "Open een vacature en maak een CV",
            "href": "/jobs",
            "detail": "Genereer een toegesneden CV of motivatiebrief.",
            "step": 4,
        }
    return {
        "label": "Bekijk je vacatures",
        "href": "/jobs",
        "detail": "Je profiel, matches en documenten zijn bij.",
        "step": 4,
    }


def _profile_context(**extra) -> dict:
    draft_pair = profile.pending_draft()
    draft = draft_pair[1] if draft_pair else None
    current = profile.read_profile()
    diff = ""
    if draft:
        diff = "\n".join(
            difflib.unified_diff(
                current.splitlines(),
                draft["profile_md"].splitlines(),
                fromfile="huidige profile.md",
                tofile="voorstel",
                lineterm="",
            )
        )
    context = {
        "profile_md": current,
        "preferences": profile.read_preferences(),
        "editor_chars": len(current),
        "editor_lines": (current.count("\n") + 1) if current else 0,
        "versions": store.list_profile_versions(20),
        "seed": profile.seed_status(),
        "bootstrap_status": store.meta_get("bootstrap_status", "idle"),
        "bootstrapped_at": store.meta_get("profile_bootstrapped_at"),
        "usage": store.usage_total(),
        "draft": draft,
        "draft_diff": diff,
        "draft_profile_text": draft["profile_md"] if draft else "",
        "draft_preferences_text": (
            json.dumps(draft["preferences"], indent=2, ensure_ascii=False) if draft else ""
        ),
        "new_answer_chars": len(profile.new_answer_log()),
        "consolidation_cost_note": profile.CONSOLIDATION_COST_NOTE,
        "private_note": "De privésectie wordt nooit naar documenten of matching gestuurd.",
    }
    context.update(extra)
    return context


# ---------------------------------------------------------------- pages


@app.get("/", response_class=HTMLResponse)
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


@app.get("/interview", response_class=HTMLResponse)
def interview_page(request: Request):
    session_id = request.query_params.get("session")
    if session_id and not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="unknown session")
    sessions = store.list_sessions(30)
    messages = _visible_messages(session_id) if session_id else []
    return templates.TemplateResponse(
        request,
        "interview.html",
        {
            "sessions": sessions,
            "session_id": session_id,
            "messages": messages,
            "coverage": interview_state.summary(),
        },
    )


@app.post("/interview/toggle")
def interview_toggle(
    topic_id: str = Form(...), status: str = Form(...), session_id: str = Form("")
):
    try:
        interview_state.toggle(topic_id, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = f"/interview?session={session_id}" if session_id else "/interview"
    return RedirectResponse(url=target, status_code=303)


@app.post("/interview/new")
def interview_new():
    mgr = get_manager()
    session_id = mgr.create_session(agent=config.AGENT_INTERVIEWER)
    title = f"Interview {now_local().strftime('%d-%m %H:%M')}"
    store.create_session(session_id, title, config.AGENT_INTERVIEWER, config.MODEL_LABEL)
    try:
        result = mgr.run_turn(session_id, config.KICKOFF_TEXT)
        _persist_turn(session_id, result, "interview", hide_kickoff=True)
    except llm.LLMError as exc:
        store.update_session(session_id, title=title + " (fout)")
        log.warning("kickoff failed: %s", exc)
    return RedirectResponse(url=f"/interview?session={session_id}", status_code=303)


@app.get("/sources", response_class=HTMLResponse)
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
            "user_sources": store.list_user_sources(),
            "jobs_by_source": store.count_jobs_by_source(),
            "scan_status": store.meta_get("scan_status", "idle"),
            "summary": summary,
            "counts": store.count_jobs(),
        },
    )


@app.post("/sources/{source_id}/toggle")
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


def _run_scan() -> dict:
    store.meta_set("scan_status", "running")
    try:
        summary = sources.scan()
        feedback.refresh_document_frequency()
        store.meta_set("scan_summary", json.dumps(summary, ensure_ascii=False))
        store.meta_set("scan_status", "done")
        if os.environ.get("MATCH_AFTER_SCAN", "1") != "0":
            for owner in store.active_owners_with_profile():
                tasks.enqueue("match", owner=owner, dedupe_key=f"match:{owner}")
        return summary
    except Exception as exc:  # noqa: BLE001 - surface failures in the UI
        log.exception("scan failed")
        store.meta_set("scan_status", f"error: {exc}"[:400])
        raise


@app.post("/sources/scan")
def sources_scan(request: Request):
    auth.require_admin(request)
    tasks.enqueue("scan", owner=None, dedupe_key="scan")
    return RedirectResponse(url="/sources", status_code=303)


@app.post("/sources/{source_id}/preference")
def source_preference(source_id: str, enabled: str = Form("1")):
    known = {source["id"] for source in sources.load_sources()}
    if source_id not in known:
        raise HTTPException(status_code=404, detail="unknown source")
    store.set_user_source(source_id, enabled == "1")
    return RedirectResponse(url="/sources", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
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
    status_links = [
        {"label": label, "value": value, "url": "/jobs?" + urlencode({**params, "status": value})}
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


@app.post("/jobs/match")
def jobs_match_route(url: str = Form("/jobs", alias="next")):
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return RedirectResponse(url="/interview", status_code=303)
    get_manager()
    owner = tenancy.user_id()
    tasks.enqueue("match", owner=owner, dedupe_key=f"match:{owner}")
    return RedirectResponse(url=_jobs_return_target(url), status_code=303)


def _run_match() -> None:
    try:
        summary = matching.match_jobs(manager)
        summary["finished_at"] = datetime.now(UTC).isoformat()
        store.meta_set("match_summary", json.dumps(summary, ensure_ascii=False))
        store.meta_set("match_status", "done")
    except Exception as exc:  # noqa: BLE001 - surface failures in the UI
        log.exception("matching failed")
        store.meta_set("match_status", f"error: {exc}"[:400])


def _doc_status(job_id: int) -> dict:
    raw = store.meta_get(f"doc_status:{job_id}")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _run_document(job_id: int, kind: str) -> None:
    key = f"doc_status:{job_id}"
    try:
        job = store.get_job(job_id)
        if not job:
            raise RuntimeError("vacature niet gevonden")
        mgr = get_manager()
        result = docs.generate_for_job(mgr, job, kind)
        store.meta_set(
            key,
            json.dumps(
                {
                    "state": "done",
                    "kind": kind,
                    "version": result.get("version"),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001 - surface failures in the UI
        log.exception("document generation failed")
        message = str(exc)
        if "provider.auth" in message or "api_key_expired" in message:
            message = "API-key verlopen of ongeldig. Werk LLM_API_KEY bij en herstart de app."
        store.meta_set(key, json.dumps({"state": "error", "kind": kind, "error": message[:300]}))


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
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


@app.post("/jobs/{job_id}/documents")
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


@app.get("/jobs/{job_id}/documents/{document_id}/{fmt}")
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


def _jobs_return_target(url: str) -> str:
    """Keep list/detail context without permitting external or unrelated redirects."""
    if (
        url != url.strip()
        or url.startswith("//")
        or "\\" in url
        or any(ord(char) < 32 for char in url)
    ):
        return "/jobs"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "/jobs"
    valid_path = parts.path == "/jobs" or (
        parts.path.startswith("/jobs/") and parts.path.removeprefix("/jobs/").isdigit()
    )
    return url if not parts.scheme and not parts.netloc and valid_path else "/jobs"


@app.post("/jobs/{job_id}/vote")
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


@app.post("/jobs/{job_id}/status")
def job_status(job_id: int, status: str = Form(...), url: str = Form("/jobs", alias="next")):
    if status not in ("new", "saved", "ignored", "applied"):
        raise HTTPException(status_code=400, detail="unknown status")
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="unknown job")
    store.update_job_status(job_id, status)
    return RedirectResponse(url=_jobs_return_target(url), status_code=303)


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    return templates.TemplateResponse(
        request,
        "profile.html",
        _profile_context(
            saved=request.query_params.get("saved", ""),
            error=request.query_params.get("error", ""),
        ),
    )


@app.post("/account/cv")
async def account_cv(request: Request, cv: UploadFile = File(...)):
    auth.require_user(request)
    if tenancy.user_id() == tenancy.LEGACY_ID:
        raise HTTPException(status_code=409, detail="Het bestaande basis-CV wordt behouden.")
    if not (cv.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Upload een DOCX-bestand.")
    raw = await cv.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Het bestand mag maximaal 5 MB zijn.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if (
                len(archive.infolist()) > 1000
                or sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024
                or "word/document.xml" not in archive.namelist()
                or any("vbaProject" in name for name in archive.namelist())
            ):
                raise ValueError("unsupported document")
    except (zipfile.BadZipFile, ValueError):
        raise HTTPException(
            status_code=400, detail="Geen geldig, macro-vrij DOCX-bestand."
        ) from None
    target = config.seed_dir() / "cv.docx"
    if target.exists():
        raise HTTPException(
            status_code=409,
            detail="Je basis-CV is al toegevoegd; bestaande documenten blijven behouden.",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    try:
        docs.ensure_templates()
    except Exception:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="Gebruik een basis-CV met de secties PROFIEL en STERKE PUNTEN (vijf opsommingstekens).",
        ) from None
    return RedirectResponse(url="/account?uploaded=1", status_code=303)


# ---------------------------------------------------------------- chat api


class SendBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@app.get("/api/tasks")
def api_tasks(request: Request):
    user = auth.require_user(request)
    return {
        "tasks": [
            {
                "id": task["id"],
                "kind": task["kind"],
                "status": task["status"],
                "error": task["error"],
            }
            for task in tasks.list_for_owner(user["id"])
        ]
    }


@app.get("/api/tasks/{task_id}")
def api_task(task_id: int, request: Request):
    user = auth.require_user(request)
    task = tasks.get(task_id, owner=user["id"])
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    result = None
    if task.get("result_json"):
        try:
            result = json.loads(task["result_json"])
        except ValueError:
            result = None
    return {
        "id": task["id"],
        "kind": task["kind"],
        "status": task["status"],
        "error": task["error"],
        "result": result,
    }


@app.get("/api/sessions")
def api_sessions():
    return {"sessions": [dict(row) for row in store.list_sessions(30)]}


@app.get("/api/sessions/{session_id}/messages")
def api_messages(session_id: str):
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="unknown session")
    return {"messages": _visible_messages(session_id)}


@app.post("/api/sessions/{session_id}/send")
def api_send(session_id: str, body: SendBody):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="unknown session")
    mgr = get_manager()
    lock = _session_lock(session_id)
    with lock:
        question = _last_assistant_text(session_id)
        try:
            result = mgr.run_turn(session_id, body.text.strip())
        except llm.LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        _persist_turn(session_id, result, "interview")
        profile.append_answer(question, body.text.strip())
        interview_state.record_answer(question, body.text.strip())
    return {"messages": _visible_messages(session_id)}


# ---------------------------------------------------------------- bootstrap


_document_lock = threading.Lock()


def _run_bootstrap() -> None:
    try:
        mgr = get_manager()
        full = store.meta_get("consolidation_full", "0") == "1"
        incremental_ready = (
            profile.profile_exists() and store.meta_get("answer_log_offset") is not None
        )
        effective_full = full or not incremental_ready
        prompt = profile.build_consolidation_prompt(full=effective_full)
        text, usage = mgr.generate(prompt, agent=config.AGENT_WRITER)
        store.record_usage(
            None,
            "consolidation",
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            float(usage.get("cost_usd") or 0.0),
            agent="writer",
        )
        try:
            data = profile.parse_consolidation(text)
        except Exception as exc:
            saved = profile.save_raw_consolidation(text)
            raise RuntimeError(f"{exc}; raw reply kept at {saved}") from exc
        draft = profile.save_draft(
            data["profile_md"],
            data["preferences"],
            {"mode": "full" if effective_full else "incremental", "usage": usage},
        )
        store.meta_set("consolidation_draft", str(draft))
        store.meta_set("bootstrap_status", "ready")
        log.info("consolidation draft ready: %s", draft)
    except Exception as exc:  # noqa: BLE001 - surface failures in the UI
        log.exception("consolidation failed")
        message = str(exc)
        if "provider.auth" in message or "api_key_expired" in message:
            message = "API-key verlopen of ongeldig. Werk LLM_API_KEY bij en herstart de app."
        store.meta_set("bootstrap_status", f"error: {message}"[:500])


@app.post("/profile/consolidate")
def profile_consolidate(full: str = Form("0")):
    get_manager()
    if tenancy.user_id() == tenancy.LEGACY_ID and not profile.seed_available():
        return RedirectResponse(url="/profile?error=seed", status_code=303)
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.read_answer_log().strip():
        return RedirectResponse(url="/interview", status_code=303)
    if profile.pending_draft():
        return RedirectResponse(url="/profile?error=draft", status_code=303)
    if store.meta_get("bootstrap_status") == "running":
        return RedirectResponse(url="/profile", status_code=303)
    store.meta_set("consolidation_full", "1" if full == "1" else "0")
    store.meta_set("bootstrap_status", "running")
    owner = tenancy.user_id()
    tasks.enqueue("consolidate", owner=owner, dedupe_key=f"consolidate:{owner}")
    return RedirectResponse(url="/profile", status_code=303)


@app.post("/profile/consolidate/accept")
def profile_accept():
    pair = profile.pending_draft()
    if pair is None:
        return RedirectResponse(url="/profile?error=draft", status_code=303)
    profile.accept_draft(pair[0])
    return RedirectResponse(url="/profile?saved=accepted", status_code=303)


@app.post("/profile/consolidate/discard")
def profile_discard():
    pair = profile.pending_draft()
    if pair is not None:
        profile.discard_draft(pair[0])
    return RedirectResponse(url="/profile?saved=discarded", status_code=303)


@app.post("/profile/consolidate/save-draft")
def profile_save_draft(request: Request, profile_md: str = Form(...), preferences: str = Form(...)):
    pair = profile.pending_draft()
    if pair is None:
        return RedirectResponse(url="/profile?error=draft", status_code=303)
    try:
        data = json.loads(preferences)
        profile.update_draft(pair[0], profile_md, data)
    except Exception as exc:  # noqa: BLE001 - show validation feedback in the form
        return templates.TemplateResponse(
            request,
            "profile.html",
            _profile_context(
                draft_error=str(exc)[:400],
                draft_profile_text=profile_md,
                draft_preferences_text=preferences,
            ),
        )
    return RedirectResponse(url="/profile?saved=draft", status_code=303)


@app.post("/profile/home")
def profile_home(
    place: str = Form(""),
    bike_minutes: int = Form(30),
    transit_minutes: int = Form(35),
    car_available: str = Form(""),
    lat: str = Form(""),
    lon: str = Form(""),
):
    label = " ".join(place.split()) or "Nog niet ingesteld"
    coords = geo.PLACES.get(label.lower())
    if coords is None:
        try:
            coords = (float(lat), float(lon)) if lat.strip() and lon.strip() else None
        except ValueError:
            coords = None
    preferences = profile.read_preferences()
    preferences["home"] = {
        "label": label,
        "lat": coords[0] if coords else 0.0,
        "lon": coords[1] if coords else 0.0,
        "bike_minutes": max(5, min(180, bike_minutes)),
        "transit_minutes": max(5, min(240, transit_minutes)),
        "car_available": car_available == "1",
    }
    profile.write_preferences(preferences)
    return RedirectResponse(url="/profile?saved=preferences", status_code=303)


@app.post("/profile/save")
def profile_save(request: Request, content: str = Form(...)):
    if len(content.strip()) < 50:
        return templates.TemplateResponse(
            request,
            "profile.html",
            _profile_context(
                profile_error="Het profiel is leeg of te kort.",
                profile_text=content,
            ),
        )
    profile.write_profile(content, note="handmatige bewerking")
    return RedirectResponse(url="/profile?saved=profile", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
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


@app.post("/profile/restore/{version_id}")
def profile_restore(version_id: int):
    versions = store.list_profile_versions(200)
    row = next((item for item in versions if item["id"] == version_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown version")
    path = Path(row["path"]).resolve()
    if not path.is_relative_to(config.user_path(profile.HISTORY_DIR).resolve()):
        raise HTTPException(status_code=400, detail="invalid archive path")
    profile.write_profile(path.read_text(), note=f"restored {path.name}")
    return RedirectResponse(url="/profile?saved=restore", status_code=303)


@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


@tasks.handler("rerank")
def _task_rerank(task):
    user = auth._get_user_by_id(task["owner"])
    if user is None:
        raise RuntimeError("unknown task owner")
    with tenancy.scope(
        {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    ):
        ranking.rerank_all()


@tasks.handler("match")
def _task_match(task):
    user = auth._get_user_by_id(task["owner"])
    if user is None:
        raise RuntimeError("unknown task owner")
    with tenancy.scope(
        {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    ):
        budget = float(os.environ.get("MATCH_DAILY_BUDGET_USD", "0.50") or 0)
        if budget > 0 and store.daily_usage_cost() >= budget:
            store.meta_set("match_status", "error: dagbudget bereikt")
            return
        _run_match()


@tasks.handler("consolidate")
def _task_consolidate(task):
    user = auth._get_user_by_id(task["owner"])
    if user is None:
        raise RuntimeError("unknown task owner")
    with tenancy.scope(
        {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    ):
        _run_bootstrap()


@tasks.handler("document")
def _task_document(task):
    user = auth._get_user_by_id(task["owner"])
    if user is None:
        raise RuntimeError("unknown task owner")
    payload = json.loads(task["payload_json"])
    with tenancy.scope(
        {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    ):
        _run_document(int(payload["job_id"]), str(payload["kind"]))


@tasks.handler("scan")
def _task_scan(task):
    if task["owner"] is not None:
        raise RuntimeError("scan tasks must be shared")
    return _run_scan()
