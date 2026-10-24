"""routers account responsibilities extracted without route changes."""

from __future__ import annotations

import io
import logging
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    RedirectResponse,
)

from .. import (
    auth,
    config,
    cv_uploads,
    tenancy,
)
from ..auth import require_user
from ..web import templates

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])


@router.post("/account/cv")
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
                or any("vbaproject" in name.lower() for name in archive.namelist())
            ):
                raise ValueError("unsupported document")
    except (zipfile.BadZipFile, ValueError):
        raise HTTPException(
            status_code=400, detail="Geen geldig, macro-vrij DOCX-bestand."
        ) from None
    if (config.seed_dir() / "cv.docx").exists():
        raise HTTPException(
            status_code=409,
            detail="Je basis-CV is al toegevoegd; bestaande documenten blijven behouden.",
        )
    try:
        preview = cv_uploads.stage(raw, cv.filename or "cv.docx", request.state.auth_session_id)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "cv_preview.html", {"error": str(exc)}, status_code=400
        )
    except Exception:
        return templates.TemplateResponse(
            request,
            "cv_preview.html",
            {"error": "Geen leesbaar DOCX-bestand. Kies een geldig bestand opnieuw."},
            status_code=400,
        )
    return templates.TemplateResponse(request, "cv_preview.html", {"preview": preview})



@router.post("/account/cv/confirm")
def account_cv_confirm(request: Request, token: str = Form(...)):
    auth.require_user(request)
    if tenancy.user_id() == tenancy.LEGACY_ID:
        raise HTTPException(status_code=409, detail="Het bestaande basis-CV wordt behouden.")
    try:
        cv_uploads.confirm(token, request.state.auth_session_id)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "cv_preview.html", {"error": str(exc)}, status_code=400
        )
    except FileExistsError as exc:
        return templates.TemplateResponse(
            request, "cv_preview.html", {"error": str(exc)}, status_code=409
        )
    except OSError:
        return templates.TemplateResponse(
            request,
            "cv_preview.html",
            {
                "error": "Opslaan is onderbroken. Controleer je account en probeer het later opnieuw."
            },
            status_code=500,
        )
    return RedirectResponse(url="/account?uploaded=1", status_code=303)



@router.post("/account/cv/cancel")
def account_cv_cancel(request: Request, token: str = Form(...)):
    auth.require_user(request)
    try:
        cv_uploads.discard(token, request.state.auth_session_id)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "cv_preview.html", {"error": str(exc)}, status_code=400
        )
    return RedirectResponse(url="/account", status_code=303)

