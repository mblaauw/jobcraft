"""The 3-minute start: CV, quick choices, real vacancies, then three real questions."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import (
    config,
    cv_text,
    cv_uploads,
    document_contact,
    feedback,
    geo,
    onboarding,
    profile,
    ranking,
    store,
    tenancy,
)
from ..auth import require_user
from ..util import now_iso
from ..web import templates
from .profile import _assess_soon

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])



def _page(request: Request, name: str, step: int | None, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"step": step, **context})








# ------------------------------------------------------------------ step 1: CV


def _resume_url() -> str:
    """The first unfinished step; a pause never sends anyone back to the beginning."""
    if store.meta_get("start_finished_at"):
        return "/jobs"
    if onboarding.swipe_queue():
        return "/start/vacatures"
    if int(store.meta_get("start_swipe_done") or 0) or onboarding.exchanges_saved():
        return "/start/vragen"
    return store.meta_get("start_stage") or ""


@router.get("/start", response_class=HTMLResponse)
def start(request: Request):
    if not store.meta_get("start_started_at"):
        store.meta_set("start_started_at", now_iso())
    target = _resume_url()
    if target:
        return RedirectResponse(url=target, status_code=303)
    return templates.TemplateResponse(request, "start_intro.html", {})


@router.get("/start/cv", response_class=HTMLResponse)
def start_cv(request: Request):
    return _page(request, "start_cv.html", 1)


def _update(form) -> bool:
    """Account sends an existing user here to replace their CV, outside the quick start."""
    return str(form.get("terug") or "") == "account"


@router.post("/start/cv", response_class=HTMLResponse)
async def start_cv_upload(request: Request, cv: UploadFile = File(...)):
    update = _update(await request.form())
    step = None if update else 1
    raw = await cv.read(cv_text.MAX_BYTES + 1)
    if len(raw) > cv_text.MAX_BYTES:
        return _page(request, "start_cv.html", step, update=update, error="Het bestand mag maximaal 5 MB zijn.")
    filename = cv.filename or ""
    display_name = tenancy.display_name()

    def read() -> tuple[dict, dict]:
        text = cv_text.extract_text(raw, filename)
        return cv_text.read_cv(text, display_name), cv_text.contact_of(text)

    try:
        found, contact = await run_in_threadpool(read)
    except ValueError as exc:
        return _page(request, "start_cv.html", step, update=update, error=str(exc))
    document_contact.suggest(contact)  # for the top of documents; it never reaches the AI
    token = ""
    can_store = tenancy.user_id() != tenancy.LEGACY_ID and filename.lower().endswith(".docx")
    if can_store and (cv_uploads.is_generated() or not (config.seed_dir() / "cv.docx").exists()):
        try:  # Only a DOCX with recognisable sections can serve as document template.
            token = cv_uploads.stage(raw, filename, request.state.auth_session_id)["token"]
        except Exception as exc:
            log.warning("uploaded CV not usable as template: %s", type(exc).__name__)
    return _page(
        request,
        "start_cv_review.html",
        step,
        update=update,
        found=found,
        titles=cv_text.SECTION_TITLES,
        cv_token=token,
    )


@router.post("/start/cv/confirm")
async def start_cv_confirm(request: Request):
    form = await request.form()
    roles = [*form.getlist("role"), *onboarding.split_terms(str(form.get("more_roles") or ""))]
    skills = [*form.getlist("skill"), *onboarding.split_terms(str(form.get("more_skills") or ""))]
    sections = {
        key: str(form.get(f"section_{key}") or "") for key in cv_text.SECTION_TITLES
    }
    updates = cv_text.profile_markdown(sections)
    if updates:
        profile.merge_sections(updates)
    preferences = profile.read_preferences()
    if roles:  # Coming back later to add a CV must not wipe the chosen functions.
        preferences["tracks"] = onboarding.tracks_for(list(dict.fromkeys(map(str, roles))), preferences["tracks"])
    boost = preferences["keywords"]["boost"]
    preferences["keywords"]["boost"] = list(dict.fromkeys([*boost, *map(str, skills)]))
    level = str(form.get("seniority") or "")
    if level in onboarding.LEVEL_WORDS:
        preferences["wishes"]["seniority"] = [level]
    place = str(form.get("place") or "").strip().lower()
    if place in geo.PLACES and not preferences["home"]["lat"]:
        lat, lon = geo.PLACES[place]
        preferences["home"].update(label=geo.PLACE_LABELS[place], lat=lat, lon=lon)
    profile.write_preferences(preferences)
    token = str(form.get("cv_token") or "")
    if token:
        try:
            cv_uploads.confirm(token, request.state.auth_session_id)
        except (ValueError, OSError) as exc:  # The profile is saved; documents can wait.
            log.info("base CV not stored during start: %s", type(exc).__name__)
    if updates and tenancy.user_id() != tenancy.LEGACY_ID:
        try:
            cv_uploads.create_generated(updates)
        except (ValueError, OSError) as exc:
            log.warning("plain base CV not created: %s", type(exc).__name__)
    if _update(form):
        return RedirectResponse(url="/account?uploaded=1", status_code=303)
    store.meta_set("start_stage", "/start/verder")
    return RedirectResponse(url="/start/verder", status_code=303)


@router.get("/start/verder", response_class=HTMLResponse)
def start_route(request: Request):
    return templates.TemplateResponse(request, "start_route.html", {})


@router.post("/start/zelf")
def start_own_vacancy():
    """Straight to pasting a vacancy; no questions and no assessment round first."""
    _finish(assess=False)
    return RedirectResponse(url="/jobs/toevoegen", status_code=303)


# ------------------------------------------------------------- step 2: choices








@router.get("/start/keuzes", response_class=HTMLResponse)
def start_choices(request: Request):
    preferences = profile.read_preferences()
    return _page(
        request,
        "start_choices.html",
        2,
        preferences=preferences,
        roles=", ".join(track["name"] for track in preferences["tracks"]),
        veto=", ".join(preferences["keywords"]["veto"]),
        hours_choice=next(
            (
                key
                for key, (low, high) in onboarding.HOURS.items()
                if (preferences["hard_filters"]["hours_min"], preferences["hard_filters"]["hours_max"])
                == (low, high)
            ),
            "any",
        ),
        travel_choices=onboarding.TRAVEL_CHOICES,
        travel_current=min(
            onboarding.TRAVEL_CHOICES, key=lambda value: abs(value - preferences["home"]["transit_minutes"])
        ),
        salary_choices=onboarding.SALARY_CHOICES,
        dealbreakers=onboarding.DEALBREAKERS,
        place_labels=sorted(geo.PLACE_LABELS.values(), key=str.casefold),
        count=onboarding.catalogue_count(
            [track["name"] for track in preferences["tracks"]],
            preferences["keywords"]["veto"],
            preferences["wishes"]["remote"],
            preferences["wishes"]["seniority"],
        ),
    )


@router.post("/start/keuzes/teller")
async def start_count(request: Request):
    form = await request.form()
    known = await run_in_threadpool(
        onboarding.save_home,
        str(form.get("place") or ""),
        onboarding.minutes_from(form.get("minutes")),
        form.get("car") == "1",
    )
    count = onboarding.catalogue_count(
        onboarding.split_terms(str(form.get("roles") or "")),
        onboarding.split_terms(str(form.get("veto") or "")),
        str(form.get("remote") or "any"),
        [str(level) for level in form.getlist("seniority")],
    )
    return {"count": count, "place_known": known}




@router.post("/start/keuzes")
async def start_choices_save(request: Request):
    form = await request.form()
    preferences = profile.read_preferences()
    roles, tracks_changed = onboarding.apply_choices(preferences, form)
    profile.write_preferences(preferences)
    await run_in_threadpool(
        onboarding.save_home,
        str(form.get("place") or ""),
        onboarding.minutes_from(form.get("minutes")),
        form.get("car") == "1",
    )
    if roles or not profile.profile_exists():
        profile.merge_sections({onboarding.ROLES_HEADING: ", ".join(roles) or "Staat nog open."})
    if store.meta_get("start_finished_at"):  # Later edit from the profile page: back to the list.
        _assess_soon("match" if tracks_changed else "rerank")
        return RedirectResponse(url="/jobs", status_code=303)
    await run_in_threadpool(ranking.rerank_all)
    store.meta_set("start_swipe", json.dumps(await run_in_threadpool(onboarding.pick_swipes)))
    store.meta_set("start_swipe_done", "0")
    return RedirectResponse(url="/start/vacatures", status_code=303)


# ---------------------------------------------------------------- step 3: swipe




@router.get("/start/vacatures", response_class=HTMLResponse)
def start_swipe(request: Request):
    queue = onboarding.swipe_queue()
    job = store.get_job(queue[0]) if queue else None
    if job is None:
        return RedirectResponse(url="/start/vragen", status_code=303)
    answered = int(store.meta_get("start_swipe_done") or 0)
    return _page(
        request,
        "start_swipe.html",
        3,
        job=job,
        excerpt=" ".join((job["description"] or "").split())[:320],
        position=answered + 1,
        total=answered + len(queue),
    )


@router.post("/start/vacatures/stoppen")
def start_swipe_stop():
    if onboarding.swipe_queue():
        store.meta_set("start_swipe", "[]")
        store.meta_set("start_swipe_done", str(int(store.meta_get("start_swipe_done") or 0) or 1))
        feedback.rebuild()
        ranking.rerank_all()
    return RedirectResponse(url="/start/vragen", status_code=303)


@router.post("/start/vacatures/{job_id}")
def start_swipe_answer(job_id: int, answer: str = Form(...)):
    queue = onboarding.swipe_queue()
    if job_id in queue:
        if answer in ("ja", "nee"):
            store.set_job_vote(job_id, 1 if answer == "ja" else -1)
        queue.remove(job_id)
        store.meta_set("start_swipe", json.dumps(queue))
        store.meta_set("start_swipe_done", str(int(store.meta_get("start_swipe_done") or 0) + 1))
        if not queue:  # Learn from all answers at once instead of after every tap.
            feedback.rebuild()
            ranking.rerank_all()
    return RedirectResponse(url="/start/vacatures", status_code=303)


# ------------------------------------------------------------ step 4: questions








@router.get("/start/vragen", response_class=HTMLResponse)
def start_questions(request: Request):
    exchanges = onboarding.exchanges_saved()
    pending = next((item for item in exchanges if item["answer"] is None), None)
    return _page(
        request,
        "start_questions.html",
        4,
        pending=pending,
        number=len(exchanges),
        total=onboarding.MAX_QUESTIONS,
        unavailable=request.query_params.get("niet") == "1",
    )


@router.post("/start/vragen/volgende")
def start_questions_next():
    exchanges = onboarding.exchanges_saved()
    if len(exchanges) >= onboarding.MAX_QUESTIONS or any(item["answer"] is None for item in exchanges):
        return RedirectResponse(url="/start/vragen", status_code=303)
    if not onboarding.ask_next(exchanges):
        return RedirectResponse(url="/start/vragen?niet=1", status_code=303)
    return RedirectResponse(url="/start/vragen", status_code=303)


@router.post("/start/vragen/antwoord")
def start_questions_answer(answer: str = Form(...)):
    exchanges = onboarding.exchanges_saved()
    pending = next((item for item in exchanges if item["answer"] is None), None)
    text = " ".join(answer.split())[:2000]
    if pending is None or not text:
        return RedirectResponse(url="/start/vragen", status_code=303)
    pending["answer"] = text
    store.meta_set("start_questions", json.dumps(exchanges, ensure_ascii=False))
    profile.append_answer(pending["question"], text)
    if len(exchanges) >= onboarding.MAX_QUESTIONS:
        return _finish()
    if not onboarding.ask_next(exchanges):
        return _finish()
    return RedirectResponse(url="/start/vragen", status_code=303)


def _finish(assess: bool = True) -> RedirectResponse:
    answered = [item for item in onboarding.exchanges_saved() if item["answer"]]
    if answered:
        profile.merge_sections(
            {
                onboarding.ANSWERS_HEADING: "\n\n".join(
                    f"**{item['question']}**\n{item['answer']}" for item in answered
                )
            }
        )
    if not profile.profile_exists():
        profile.merge_sections({onboarding.ROLES_HEADING: "Staat nog open."})
    if not store.meta_get("start_finished_at"):
        store.meta_set("start_finished_at", now_iso())
        store.count_event("profile_finished")
    store.meta_set("start_questions", "[]")
    store.meta_set("start_swipe", "[]")
    if assess:
        _assess_soon()
    return RedirectResponse(url="/jobs?welkom=1", status_code=303)


@router.post("/start/klaar")
def start_finish():
    return _finish()
