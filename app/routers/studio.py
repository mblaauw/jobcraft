"""The application studio for one vacancy: plan, CV and letter side by side."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import ValidationError

from .. import (
    application_plan,
    background_work,
    config,
    document_contact,
    document_design,
    document_pipeline,
    profile,
    runtime,
    store,
    tasks,
    tenancy,
    vacancy_reader,
)
from ..ai_budget import budget_reached
from ..auth import require_user
from ..document_content import CvContent, LetterContent
from ..web import templates
from ..workflows import _doc_status, _document_lock

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])

KINDS = {"cv": "resume", "brief": "motivatie"}
TAB_OF = {"resume": "cv", "motivatie": "brief"}
PREVIEW_DPI = "110"
STALE_SECONDS = 15 * 60


def _job(job_id: int) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    return dict(job)


def _here(job_id: int, tab: str = "", **params) -> str:
    query = {key: value for key, value in {"tab": tab, **params}.items() if value}
    return f"/jobs/{job_id}/sollicitatie" + (f"?{urlencode(query)}" if query else "")


def _status(job_id: int) -> dict:
    """A task that went silent long ago must not lock the page forever."""
    status = _doc_status(job_id)
    if status.get("state") == "running":
        try:
            started = datetime.fromisoformat(status.get("updated_at") or "")
            if (datetime.now(UTC) - started).total_seconds() > STALE_SECONDS:
                return {"state": "error", "error": "Dit duurde te lang. Probeer het opnieuw."}
        except ValueError:
            pass
    return status


def _meta(row) -> dict:
    try:
        return json.loads(row["meta_json"] or "{}")
    except ValueError:
        return {}


def _versions(job_id: int, kind: str) -> list[dict]:
    """Newest first, only those made by the studio's own format."""
    found = []
    for row in store.list_documents(job_id):
        if row["kind"] != kind:
            continue
        meta = _meta(row)
        if meta.get("format") == 2:
            found.append({"row": row, "meta": meta})
    return sorted(found, key=lambda item: item["row"]["version"], reverse=True)


def _enqueue(job_id: int, kind: str) -> str:
    """Empty on success, otherwise what to tell the person."""
    if not config.LLM_API_KEY and getattr(runtime.manager, "client", None) is None:
        return "De AI-dienst is nu niet beschikbaar. Probeer het later opnieuw."
    if budget_reached():
        return "Je daglimiet voor AI is bereikt. Morgen kan het weer."
    with _document_lock:
        if _status(job_id).get("state") == "running":
            return ""
        store.meta_set(
            f"doc_status:{job_id}",
            json.dumps(
                {
                    "state": "running",
                    "kind": kind,
                    "stage": "queued",
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
    owner = tenancy.user_id()
    store.count_event("documents_requested")
    tasks.enqueue(
        "document",
        owner=owner,
        payload={"job_id": job_id, "kind": kind},
        dedupe_key=f"document:{owner}:{job_id}:{kind}",
    )
    return ""


@router.get("/jobs/{job_id}/sollicitatie", response_class=HTMLResponse)
def studio(request: Request, job_id: int):
    job = _job(job_id)
    plan = application_plan.load(job_id)
    background_work.mark_seen(job_id)
    versions = {kind: _versions(job_id, kind) for kind in KINDS.values()}
    has_documents = any(versions.values())
    tab = request.query_params.get("tab", "")
    if tab not in ("plan", "cv", "brief"):
        tab = "cv" if has_documents else "plan"
    current = None
    if tab in KINDS:
        rows = versions[KINDS[tab]]
        wanted = request.query_params.get("versie", "")
        current = next((item for item in rows if str(item["row"]["id"]) == wanted), rows[0] if rows else None)
    contact = document_contact.load()
    status = _status(job_id)
    return templates.TemplateResponse(
        request,
        "studio.html",
        {
            "job": job,
            "plan": plan,
            "tab": tab,
            "versions": versions,
            "has_documents": has_documents,
            "current": current,
            "status": status,
            "running": status.get("state") == "running",
            "problem": request.query_params.get("probleem", ""),
            "saved": request.query_params.get("opgeslagen", ""),
            "contact": contact,
            "contact_missing": document_contact.missing(contact),
            "has_profile": profile.profile_exists(),
            "stories": application_plan.stories(),
            "designs": document_design.DESIGNS,
            "colours": document_design.COLOURS,
            "own_layout": document_pipeline.own_layout_available(),
            "tones": document_pipeline.TONES,
            "can_preview": bool(shutil.which("pdftoppm")),
        },
    )


@router.post("/jobs/{job_id}/sollicitatie/plan")
def make_plan(job_id: int):
    _job(job_id)
    if not profile.profile_exists():
        return RedirectResponse(url="/start", status_code=303)
    problem = _enqueue(job_id, "plan")
    return RedirectResponse(url=_here(job_id, "plan", probleem=problem), status_code=303)


@router.post("/jobs/{job_id}/sollicitatie/schrijven")
async def write(request: Request, job_id: int):
    """Keep the choices from the plan, then write what was asked for."""
    _job(job_id)
    form = await request.form()
    answers = {
        key.removeprefix("antwoord_"): str(value)
        for key, value in form.items()
        if key.startswith("antwoord_")
    }
    application_plan.choose(job_id, str(form.get("invalshoek") or ""), answers, str(form.get("toelichting") or ""))
    kind = str(form.get("wat") or "both")
    if kind not in ("both", "resume", "motivatie"):
        raise HTTPException(status_code=400, detail="unknown document kind")
    problem = await run_in_threadpool(_enqueue, job_id, kind)
    return RedirectResponse(url=_here(job_id, "plan", probleem=problem), status_code=303)


@router.post("/jobs/{job_id}/sollicitatie/gegevens")
async def save_contact(request: Request, job_id: int):
    _job(job_id)
    form = await request.form()
    document_contact.save({key: str(form.get(key) or "") for key in document_contact.FIELDS})
    target = str(form.get("tab") or "")
    return RedirectResponse(
        url=_here(job_id, target if target in ("plan", "cv", "brief") else "", opgeslagen="gegevens"),
        status_code=303,
    )


@router.post("/jobs/{job_id}/sollicitatie/verhaal/{index}/verwijderen")
def remove_story(job_id: int, index: int):
    _job(job_id)
    application_plan.remove_story(index)
    return RedirectResponse(url=_here(job_id, "plan"), status_code=303)


def _document(job_id: int, document_id: int) -> tuple[dict, dict]:
    row = store.get_document(document_id)
    if not row or row["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="unknown document")
    meta = _meta(row)
    if meta.get("format") != 2:
        raise HTTPException(status_code=409, detail="Maak voor dit oudere document een nieuwe versie.")
    return dict(row), meta


def _after(job_id: int, kind: str, **params) -> RedirectResponse:
    return RedirectResponse(url=_here(job_id, TAB_OF[kind], **params), status_code=303)


@router.post("/jobs/{job_id}/sollicitatie/documenten/{document_id}/opmaak")
def restyle(job_id: int, document_id: int, design: str = Form(""), colour: str = Form("")):
    """A different look costs no AI: the same words are laid out again as a new version."""
    job = _job(job_id)
    row, meta = _document(job_id, document_id)
    allowed = set(document_design.DESIGNS) | {document_pipeline.OWN_LAYOUT}
    if design not in allowed or colour not in document_design.COLOURS:
        raise HTTPException(status_code=400, detail="unknown style")
    if design == document_pipeline.OWN_LAYOUT and (
        row["kind"] != "resume" or not document_pipeline.own_layout_available()
    ):
        raise HTTPException(status_code=400, detail="unknown style")
    if design != document_pipeline.OWN_LAYOUT:
        store.meta_set(document_pipeline.STYLE_KEY, json.dumps({"design": design, "colour": colour}))
    document_pipeline.save_version(
        job, row["kind"], meta["content"], design=design, colour=colour, review=meta.get("review")
    )
    return _after(job_id, row["kind"])


def _lines(raw: object) -> list[str]:
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def _edited_cv(form) -> dict:
    roles = []
    for index in range(int(form.get("rollen") or 0)):
        role = str(form.get(f"rol_{index}") or "").strip()
        if role:
            roles.append(
                {
                    "role": role,
                    "organisation": str(form.get(f"organisatie_{index}") or "").strip(),
                    "period": str(form.get(f"periode_{index}") or "").strip(),
                    "bullets": _lines(form.get(f"punten_{index}")),
                }
            )
    education = []
    for line in _lines(form.get("opleiding")):
        parts = [part.strip() for part in line.split("|")]
        education.append({"title": parts[0], "organisation": parts[1] if len(parts) > 1 else "", "period": parts[2] if len(parts) > 2 else ""})
    return {
        "headline": str(form.get("kop") or "").strip(),
        "profile": str(form.get("profiel") or "").strip(),
        "highlights": _lines(form.get("hoogtepunten")),
        "experience": roles,
        "education": education,
        "skills": _lines(form.get("vaardigheden")),
        "languages": _lines(form.get("talen")),
    }


@router.post("/jobs/{job_id}/sollicitatie/documenten/{document_id}/tekst")
async def save_text(request: Request, job_id: int, document_id: int):
    job = _job(job_id)
    row, meta = _document(job_id, document_id)
    form = await request.form()
    try:
        if row["kind"] == "resume":
            content = CvContent.model_validate(_edited_cv(form)).model_dump()
        else:
            content = LetterContent.model_validate(
                {
                    "salutation": str(form.get("aanhef") or "").strip(),
                    "paragraphs": [
                        part.strip()
                        for part in str(form.get("tekst") or "").replace("\r\n", "\n").split("\n\n")
                        if part.strip()
                    ],
                }
            ).model_dump()
    except (ValidationError, ValueError):
        return _after(
            job_id,
            row["kind"],
            probleem="Dit kon niet worden opgeslagen. Vul kop en tekst in en houd het profiel tussen de 60 en 800 tekens.",
        )
    await run_in_threadpool(
        document_pipeline.save_version,
        job,
        row["kind"],
        content,
        design=meta.get("design"),
        colour=meta.get("colour"),
        extra={"edited": True},
    )
    return _after(job_id, row["kind"], opgeslagen="tekst")


@router.post("/jobs/{job_id}/sollicitatie/documenten/{document_id}/toon")
def retone(job_id: int, document_id: int, tone: str = Form("")):
    """In the background like all writing, so nobody has to stay on this page."""
    if tone not in document_pipeline.TONES:
        raise HTTPException(status_code=400, detail="unknown tone")
    _job(job_id)
    row, _meta_data = _document(job_id, document_id)
    problem = _enqueue(job_id, f"tone:{tone}:{document_id}")
    return _after(job_id, row["kind"], probleem=problem)


@router.get("/jobs/{job_id}/tekst", response_class=HTMLResponse)
def job_text(request: Request, job_id: int):
    """Just the vacancy text, for the page to swap in once the AI has read it."""
    job = _job(job_id)
    return templates.TemplateResponse(
        request, "_vacancy_fragment.html", {"job": job, "vacancy": vacancy_reader.view(job)}
    )


@router.get("/api/werk")
def work_status():
    """Polled by every page while something runs, so a finished document can be announced."""
    return background_work.overview()


@router.get("/jobs/{job_id}/sollicitatie/documenten/{document_id}/voorbeeld/{page}.png")
def preview(job_id: int, document_id: int, page: int):
    """An image of the real PDF, so what you see is exactly what an employer gets."""
    row, _meta_data = _document(job_id, document_id)
    if not row["pdf_path"] or not 1 <= page <= 4 or not shutil.which("pdftoppm"):
        raise HTTPException(status_code=404, detail="no preview")
    pdf = (config.DATA_DIR / row["pdf_path"]).resolve()
    if not pdf.is_relative_to(config.DATA_DIR.resolve()) or not pdf.exists():
        raise HTTPException(status_code=404, detail="no preview")
    stem = pdf.with_suffix("")
    image = Path(f"{stem}-p{page}.png")
    if not image.exists():
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", PREVIEW_DPI, "-f", str(page), "-l", str(page), "-singlefile", str(pdf), str(image.with_suffix(""))],
                check=True,
                timeout=30,
                capture_output=True,
            )
        except (subprocess.SubprocessError, OSError):
            raise HTTPException(status_code=404, detail="no preview") from None
    return FileResponse(image, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
