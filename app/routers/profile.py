"""routers profile responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
)

from .. import (
    config,
    geo,
    profile,
    store,
    tasks,
    tenancy,
)
from ..auth import require_user
from ..runtime import get_manager
from ..web import templates
from ..web_helpers import _profile_context

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    return templates.TemplateResponse(
        request,
        "profile.html",
        _profile_context(
            saved=request.query_params.get("saved", ""),
            error=request.query_params.get("error", ""),
        ),
    )



@router.post("/profile/consolidate")
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



@router.post("/profile/consolidate/accept")
def profile_accept():
    pair = profile.pending_draft()
    if pair is None:
        return RedirectResponse(url="/profile?error=draft", status_code=303)
    profile.accept_draft(pair[0])
    return RedirectResponse(url="/profile?saved=accepted", status_code=303)



@router.post("/profile/consolidate/discard")
def profile_discard():
    pair = profile.pending_draft()
    if pair is not None:
        profile.discard_draft(pair[0])
    return RedirectResponse(url="/profile?saved=discarded", status_code=303)



@router.post("/profile/consolidate/save-draft")
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



@router.post("/profile/home")
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
    if (lat.strip() or lon.strip()) and (coords is None or not geo.valid_coordinates(*coords)):
        raise HTTPException(status_code=400, detail="Vul geldige breedte- en lengtecoördinaten in.")
    if coords is not None and not geo.valid_coordinates(*coords):
        raise HTTPException(status_code=400, detail="Vul geldige breedte- en lengtecoördinaten in.")
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



@router.post("/profile/save")
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



@router.post("/profile/restore/{version_id}")
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

