"""Everything after finding a job: the board, adding a job, insights, editing, your data."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import (
    account_data,
    applications,
    auth,
    document_edits,
    insights,
    job_import,
    notifications,
    store,
    tenancy,
)
from ..auth import require_user
from ..auth.credentials import verify_password
from ..auth.users import _deactivate_without_losing_last_admin, _get_user_by_id
from ..web import templates
from ..web_helpers import _jobs_return_target

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])


def _job_or_404(job_id: int):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    return job


# ---------------------------------------------------------------- the board


@router.get("/sollicitaties", response_class=HTMLResponse)
def board_page(request: Request):
    columns = applications.board()
    return templates.TemplateResponse(
        request,
        "board.html",
        {"columns": columns, "statuses": applications.COLUMNS, "funnel": applications.funnel(columns)},
    )


@router.post("/sollicitaties/{job_id}")
def board_update(
    job_id: int, status: str = Form(...), deadline: str = Form(""), note: str = Form("")
):
    _job_or_404(job_id)
    if status not in applications.STATUSES:
        raise HTTPException(status_code=400, detail="unknown status")
    store.update_job_status(job_id, status)
    applications.save_details(job_id, deadline, note, status)
    if status == "applied":
        store.count_event("applications")
    return RedirectResponse(url="/sollicitaties", status_code=303)


# -------------------------------------------------------- one vacancy at a time


@router.get("/jobs/doorlopen", response_class=HTMLResponse)
def jobs_swipe(request: Request, overslaan: int = 0):
    skip = max(0, min(overslaan, 1000))
    rows = store.list_jobs_page(
        1, skip, "new", sort="rank", within_travel=True, only_unvoted=True
    )
    job = rows[0] if rows else None
    return templates.TemplateResponse(
        request,
        "jobs_swipe.html",
        {
            "job": job,
            "excerpt": " ".join(((job or {}).get("description") or "").split())[:320],
            "skip": skip,
            "here": f"/jobs/doorlopen?{urlencode({'overslaan': skip})}",
        },
    )


@router.get("/jobs/nieuw", response_class=HTMLResponse)
def jobs_new(request: Request):
    jobs = notifications.pending()
    notifications.mark_seen()
    return templates.TemplateResponse(request, "jobs_new.html", {"jobs": jobs})


# ---------------------------------------------------------------- add a vacancy


@router.get("/jobs/toevoegen", response_class=HTMLResponse)
def import_page(request: Request):
    return templates.TemplateResponse(request, "job_import.html", {})


@router.post("/jobs/toevoegen", response_class=HTMLResponse)
async def import_submit(request: Request):
    form = await request.form()
    url = str(form.get("url") or "").strip()
    text = str(form.get("description") or "").strip()[:30000]
    title = str(form.get("title") or "").strip()[:200]
    if text:
        fields = {
            "title": title or text.split("\n")[0][:120],
            "company": str(form.get("company") or "").strip()[:120],
            "location": str(form.get("location") or "").strip()[:120],
            "description": text,
        }
    else:
        try:
            fields = await run_in_threadpool(job_import.fetch_page, url)
        except job_import.ImportBlocked as exc:
            return templates.TemplateResponse(
                request, "job_import.html", {"error": str(exc), "url": url}, status_code=400
            )
    job_id = job_import.add(fields, url)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ---------------------------------------------------------------- insights


@router.post("/jobs/{job_id}/inzicht/{kind}")
def job_insight(job_id: int, kind: str, url: str = Form("", alias="next")):
    """Queued, not awaited: the page shows it is running and fills in by itself."""
    if kind not in insights.KINDS:
        raise HTTPException(status_code=404, detail="unknown insight")
    job = _job_or_404(job_id)
    target = _jobs_return_target(url) if url else f"/jobs/{job_id}"
    if not target.startswith(f"/jobs/{job_id}"):
        target = f"/jobs/{job_id}"
    if problem := insights.request(kind, job):
        separator = "&" if "?" in target else "?"
        target += f"{separator}inzicht_fout={problem}"
    return RedirectResponse(url=f"{target}#inzicht", status_code=303)


# ---------------------------------------------------------- editing documents


def _editable(job_id: int, document_id: int) -> tuple[dict, dict]:
    document = store.get_document(document_id)
    if not document or document["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="unknown document")
    meta = json.loads(document["meta_json"] or "{}")
    if not meta.get("content"):
        raise HTTPException(
            status_code=409, detail="Dit oudere document kan niet worden bewerkt. Maak een nieuwe versie."
        )
    return dict(document), meta


def _old_format_only(job_id: int, meta: dict) -> RedirectResponse | None:
    """Documents from the studio are edited there; these pages know only the old shape."""
    if meta.get("format") == 2:
        return RedirectResponse(url=f"/jobs/{job_id}/sollicitatie", status_code=303)
    return None


@router.get("/jobs/{job_id}/documents/{document_id}/bewerken", response_class=HTMLResponse)
def document_edit(request: Request, job_id: int, document_id: int):
    job = _job_or_404(job_id)
    document, meta = _editable(job_id, document_id)
    if moved := _old_format_only(job_id, meta):
        return moved
    return templates.TemplateResponse(
        request,
        "document_edit.html",
        {"job": job, "document": document, "content": meta["content"], "error": ""},
    )


@router.post("/jobs/{job_id}/documents/{document_id}/bewerken", response_class=HTMLResponse)
async def document_save(request: Request, job_id: int, document_id: int):
    job = _job_or_404(job_id)
    document, _meta = _editable(job_id, document_id)
    if moved := _old_format_only(job_id, _meta):
        return moved
    form = await request.form()
    if document["kind"] == "resume":
        content = {
            "profile": str(form.get("profile") or "").strip(),
            "strong_points": [
                line.strip() for line in str(form.get("strong_points") or "").splitlines() if line.strip()
            ],
        }
    else:
        content = {
            "paragraphs": [
                part.strip() for part in str(form.get("text") or "").split("\n\n") if part.strip()
            ]
        }
    try:
        await run_in_threadpool(document_edits.save_edited_version, job, document["kind"], content)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "document_edit.html",
            {"job": job, "document": document, "content": content, "error": document_edits.edit_problem(exc)},
            status_code=400,
        )
    return RedirectResponse(url=f"/jobs/{job_id}#documenten", status_code=303)


@router.post("/jobs/{job_id}/documents/{document_id}/herschrijven")
def document_rewrite(job_id: int, document_id: int, style: str = Form("")):
    if style not in document_edits.REWRITE_STYLES:
        raise HTTPException(status_code=400, detail="unknown style")
    job = _job_or_404(job_id)
    document, meta = _editable(job_id, document_id)
    if moved := _old_format_only(job_id, meta):
        return moved
    problem = ""
    try:
        if document_edits.rewrite(job, document["kind"], meta["content"], style) is None:
            problem = "Herschrijven kan nu niet. Probeer het later opnieuw of bewerk de tekst zelf."
    except Exception:
        log.exception("document rewrite failed")
        problem = "Herschrijven is niet gelukt. Je document is niet veranderd."
    query = f"?{urlencode({'doc_error': problem})}" if problem else ""
    return RedirectResponse(url=f"/jobs/{job_id}{query}#documenten", status_code=303)


@router.post("/jobs/{job_id}/documents/{document_id}/verwijderen")
def document_remove(job_id: int, document_id: int, url: str = Form("", alias="next")):
    _job_or_404(job_id)
    kind = document_edits.remove(job_id, document_id)
    if kind is None:
        raise HTTPException(status_code=404, detail="unknown document")
    if url.startswith(f"/jobs/{job_id}/sollicitatie"):
        tab = "cv" if kind == "resume" else "brief"
        return RedirectResponse(url=f"/jobs/{job_id}/sollicitatie?tab={tab}&verwijderd=1", status_code=303)
    return RedirectResponse(url=f"/jobs/{job_id}?verwijderd=1#documenten", status_code=303)


@router.get("/jobs/{job_id}/documents/{document_id}/verschil", response_class=HTMLResponse)
def document_changes(request: Request, job_id: int, document_id: int):
    job = _job_or_404(job_id)
    document, meta = _editable(job_id, document_id)
    earlier = [
        row
        for row in store.list_documents(job_id)
        if row["kind"] == document["kind"]
        and row["version"] < document["version"]
        and json.loads(row["meta_json"] or "{}").get("content")
    ]
    if not earlier:
        raise HTTPException(status_code=404, detail="Er is geen eerdere versie om mee te vergelijken.")
    previous = max(earlier, key=lambda row: row["version"])
    return templates.TemplateResponse(
        request,
        "document_diff.html",
        {
            "job": job,
            "document": document,
            "previous": previous,
            "lines": document_edits.changes(
                json.loads(previous["meta_json"])["content"], meta["content"]
            ),
        },
    )


# ---------------------------------------------------------------- your data


@router.post("/account/export")
def account_export() -> Response:
    return Response(
        account_data.export_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="jobcraft-gegevens.zip"'},
    )


@router.post("/account/verwijderen")
def account_delete(request: Request, password: str = Form(""), confirm: str = Form("")):
    user = auth.require_user(request)
    row = _get_user_by_id(user["id"])
    if tenancy.user_id() == tenancy.LEGACY_ID or row is None:
        raise HTTPException(status_code=409, detail="Dit account kan hier niet worden verwijderd.")
    if confirm.strip().upper() != "VERWIJDER" or not verify_password(password, row["password_hash"]):
        raise HTTPException(
            status_code=400, detail="Vul je wachtwoord in en typ VERWIJDER om te bevestigen."
        )
    problem = _deactivate_without_losing_last_admin(user["id"])
    if problem:
        raise HTTPException(status_code=409, detail="De laatste beheerder kan niet worden verwijderd.")
    account_data.delete_account()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@router.post("/account/dagmail")
def account_digest(email: str = Form(""), enabled: str = Form("")):
    store.meta_set("digest_email", email.strip()[:200])
    store.meta_set("digest_enabled", "1" if enabled == "1" and "@" in email else "0")
    return RedirectResponse(url="/account?dagmail=1", status_code=303)
