"""Mijn cv: edit the CV part by part, read one in, and act on the coach's notes."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from .. import application_plan, cv_ai, cv_model, cv_text
from ..auth import require_user

router = APIRouter(dependencies=[Depends(require_user)])

HERE = "/profile?tab=cv"


def _back(anchor: str = "", **params) -> RedirectResponse:
    query = urlencode({key: value for key, value in params.items() if value})
    query = f"&{query}" if query else ""
    return RedirectResponse(url=f"{HERE}{query}{'#' + anchor if anchor else ''}", status_code=303)


def _cv() -> dict:
    data = cv_model.load()
    if data is None:
        raise HTTPException(status_code=409, detail="Er is nog geen cv.")
    return data


def _anchor(data: dict, target: str) -> str:
    """Where a note sits on the page, so acting on it keeps you in place."""
    if cv_model.find_role(data, target):
        return target
    if hit := cv_model.find_point(data, target):
        return hit[0]["id"]
    if target in cv_model.LISTS:
        return f"cv-{target}"
    return "cv-summary" if target == "summary" else "cv-education"


@router.post("/profile/cv/inlezen")
async def read_in(bron: str = Form("tekst"), tekst: str = Form(""), bestand: UploadFile | None = File(None)):
    raw = await bestand.read(cv_text.MAX_BYTES + 1) if bestand and bestand.filename else b""
    if len(raw) > cv_text.MAX_BYTES:
        return _back("inlezen", probleem="Het bestand mag maximaal 5 MB zijn.")
    choice = "bestand" if raw else ("profiel" if bron == "profiel" else "tekst")
    try:
        text = await run_in_threadpool(cv_ai.source_text, choice, tekst, raw, bestand.filename if bestand else "")
    except ValueError as exc:
        return _back("inlezen", probleem=str(exc))
    return _back(probleem=cv_ai.request("import", text=text, source=choice))


@router.post("/profile/cv/omzetting")
def conversion(wat: str = Form(...)):
    found = cv_ai.draft()
    if found and wat == "overnemen":
        cv_model.save(found["data"], note="cv ingelezen")
        cv_ai.discard_draft()
        cv_ai.request("coach")  # the coach reads the new CV straight away
        return _back(opgeslagen="cv")
    cv_ai.discard_draft()
    return _back()


@router.post("/profile/cv/over-mij")
def save_summary(tekst: str = Form("")):
    data = _cv()
    data["summary"] = tekst.replace("\r\n", "\n").strip()
    cv_model.save(data, note="cv: over mij")
    return _back("cv-summary", opgeslagen="deel")


@router.post("/profile/cv/functie")
def save_role(
    id: str = Form(""),
    functie: str = Form(...),
    organisatie: str = Form(""),
    plaats: str = Form(""),
    periode: str = Form(""),
    punten: str = Form(""),
):
    data = _cv()
    if not functie.strip():
        return _back("cv-roles", probleem="Vul de functie in.")
    role = cv_model.find_role(data, id) if id else None
    if id and role is None:
        raise HTTPException(status_code=404, detail="unknown role")
    if role is None:
        role = {"id": cv_model.new_id("r"), "points": []}
        data["roles"].insert(0, role)
    role.update(title=functie.strip(), organisation=organisatie.strip(), place=plaats.strip(), period=periode.strip())
    role["points"] = cv_model.points_from_lines(role["points"], punten)
    cv_model.save(data, note=f"cv: {role['title']}"[:80])
    return _back(role["id"], opgeslagen="deel")


@router.post("/profile/cv/functie/{role_id}/{actie}")
def role_action(role_id: str, actie: str):
    data = _cv()
    roles = data["roles"]
    index = next((i for i, role in enumerate(roles) if role["id"] == role_id), None)
    if index is None or actie not in ("verwijderen", "omhoog", "omlaag"):
        raise HTTPException(status_code=404, detail="unknown role")
    if actie == "verwijderen":
        roles.pop(index)
    else:
        other = index - 1 if actie == "omhoog" else index + 1
        if 0 <= other < len(roles):
            roles[index], roles[other] = roles[other], roles[index]
    cv_model.save(data, note="cv: werkervaring")
    return _back("cv-roles", opgeslagen="deel")


@router.post("/profile/cv/opleiding")
def save_education(tekst: str = Form("")):
    data = _cv()
    education = []
    for line in cv_model.lines(tekst):
        parts = [part.strip() for part in line.split("|")]
        education.append({"id": cv_model.new_id("e"), "title": parts[0], "school": parts[1] if len(parts) > 1 else "", "period": parts[2] if len(parts) > 2 else ""})
    data["education"] = education
    cv_model.save(data, note="cv: opleiding")
    return _back("cv-education", opgeslagen="deel")


@router.post("/profile/cv/lijst/{key}")
def save_list(key: str, tekst: str = Form("")):
    if key not in cv_model.LISTS:
        raise HTTPException(status_code=404, detail="unknown list")
    data = _cv()
    data[key] = cv_model.lines(tekst)
    cv_model.save(data, note=f"cv: {cv_model.LISTS[key].lower()}")
    return _back(f"cv-{key}", opgeslagen="deel")


@router.post("/profile/cv/coach")
def ask_coach():
    _cv()
    return _back("coach", probleem=cv_ai.request("coach"))


@router.post("/profile/cv/coach/{note_id}")
def act_on_note(note_id: str, actie: str = Form(...), antwoord: str = Form("")):
    data = _cv()
    found = cv_ai.notes()
    note = next((item for item in found.get("notes", []) if item["id"] == note_id), None)
    if note is None:
        raise HTTPException(status_code=404, detail="unknown note")
    target = note["target"]
    place = _anchor(data, target)  # before a removed point takes its place with it
    changed = False
    if actie == "antwoord":
        answer = " ".join(antwoord.split())
        if not answer:
            return _back(place, probleem="Schrijf eerst je antwoord.")
        role = cv_model.find_role(data, target) or (cv_model.find_point(data, target) or (None, None))[0]
        if role is not None:
            role["points"].append({"id": cv_model.new_id("p"), "text": answer, "source": "antwoord"})
        elif target == "summary":
            data["summary"] = f"{data.get('summary', '').rstrip()} {answer}".strip()
        else:
            data.setdefault("extras", []).append(answer)
        application_plan.add_story(note["text"], answer)  # every next application can use it too
        changed = True
    elif actie == "overnemen" and note["kind"] == "sterker" and (hit := cv_model.find_point(data, target)):
        hit[1].update(text=note["suggestion"].strip(), source="coach")
        changed = True
    elif actie == "weghalen" and note["kind"] == "niet_claimen" and (hit := cv_model.find_point(data, target)):
        hit[0]["points"].remove(hit[1])
        changed = True
    elif actie != "laat":
        raise HTTPException(status_code=400, detail="unknown action")
    note["state"] = "done" if changed else "dismissed"
    if changed:
        cv_model.save(data, note="cv: coach")
    cv_ai.save_notes(found["notes"], cv_model.digest(data) if changed else found.get("digest", ""))
    return _back(place)

