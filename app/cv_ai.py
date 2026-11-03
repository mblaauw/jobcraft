"""The two AI jobs behind Mijn cv: reading a CV into parts, and the coach that reads along.

Both run in the background. Contact details are removed before anything is sent, and
the coach may only suggest what the person's own sources support.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from . import (
    application_plan,
    config,
    cv_model,
    cv_text,
    document_contact,
    document_content,
    profile,
    runtime,
    store,
    tasks,
    tenancy,
)
from .ai_budget import budget_reached
from .runtime import get_manager
from .util import now_iso

log = logging.getLogger("jobcraft")

STATUS_KEY = "cv_status"
TEXT_KEY = "cv_import_text"  # the text to read waits here, not in the task queue
DRAFT_KEY = "cv_draft"
COACH_KEY = "cv_coach"
STALE_SECONDS = 10 * 60
MAX_NOTES = 12
KINDS = ("vraag", "klopt_niet", "sterker", "niet_claimen")


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    kind: str
    text: str
    suggestion: str = ""


class CoachNotes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: list[Note] = Field(default_factory=list)


# ------------------------------------------------------------------ status


def status() -> dict:
    try:
        data = json.loads(store.meta_get(STATUS_KEY) or "{}")
    except ValueError:
        return {}
    if data.get("state") == "running":
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(data.get("updated_at") or "")).total_seconds()
        except ValueError:
            age = 0
        if age > STALE_SECONDS:
            return {"state": "error", "kind": data.get("kind"), "error": "Dit duurde te lang. Probeer het opnieuw."}
    return data


def _set_status(state: str, kind: str, **extra) -> None:
    store.meta_set(STATUS_KEY, json.dumps({"state": state, "kind": kind, "updated_at": now_iso(), **extra}))


def request(kind: str, text: str = "", source: str = "") -> str:
    """Queue an import or a coach run; returns what to tell the person, or empty."""
    if status().get("state") == "running":
        return ""
    if not config.LLM_API_KEY and getattr(runtime.manager, "client", None) is None:
        return "De AI-dienst is nu niet beschikbaar. Probeer het later opnieuw."
    if budget_reached():
        return "Je daglimiet voor AI is bereikt. Morgen kan het weer."
    if text:
        store.meta_set(TEXT_KEY, json.dumps({"text": text, "source": source}, ensure_ascii=False))
    _set_status("running", kind)
    owner = tenancy.user_id()
    tasks.enqueue(f"cv_{kind}", owner=owner, dedupe_key=f"cv:{owner}:{kind}")
    return ""


# ------------------------------------------------------------------ reading a CV


def source_text(choice: str, pasted: str = "", raw: bytes = b"", filename: str = "") -> str:
    """The text to read, without name and contact details. Raises ValueError with a message."""
    if choice == "profiel":
        text = profile.public_excerpt(profile.read_profile(), 20000)
    elif choice == "bestand":
        text = cv_text.extract_text(raw, filename)
    else:
        text = pasted
    if choice != "profiel":
        document_contact.suggest(cv_text.contact_of(text))  # for the top of documents only
    text = cv_text.redact(text[:cv_text.MAX_CHARS], [tenancy.display_name()])
    if len(text.strip()) < 80:
        raise ValueError("Hier staat te weinig tekst in om een cv uit te halen.")
    return text


def read(mgr, text: str) -> dict:
    return application_plan.call_json(
        mgr,
        "## Tekst\n" + text + "\n\nZet dit om naar het cv-formaat. Geef JSON met: summary (de profieltekst bovenaan, "
        "letterlijk), roles (elk {title, organisation, place, period, points}; period zoals 'jun 2024 – heden'; "
        "points zijn de losse punten, letterlijk), education (elk {title, school, period}), skills, languages "
        "(zoals 'Engels — vloeiend'), extras (rijbewijs, certificaten, systemen), interests.",
        agent="cv_reader",
        model=cv_model.ReadCv,
        ref="cv-import",
    )


def draft() -> dict | None:
    try:
        data = json.loads(store.meta_get(DRAFT_KEY) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def discard_draft() -> None:
    store.meta_set(DRAFT_KEY, "null")


# ------------------------------------------------------------------ the coach


def _compact(data: dict) -> str:
    """The CV with its ids, so every note can point at the exact part it is about."""
    out = [f"[summary] {data.get('summary') or '(leeg)'}"]
    for role in data.get("roles") or []:
        out.append(f"[{role['id']}] {cv_model.role_line(role)}")
        out.extend(f"  [{point['id']}] {point['text']}" for point in role["points"])
    for item in data.get("education") or []:
        out.append(f"[{item['id']}] Opleiding: {item['title']}, {item.get('school', '')}, {item.get('period', '')}")
    for key, heading in cv_model.LISTS.items():
        out.append(f"[{key}] {heading}: " + "; ".join(data.get(key) or []))
    return "\n".join(out)


def _other_sources() -> str:
    """What else the person told us, to find what contradicts or is missing."""
    rest = [
        f"## {item['heading']}\n{item['text']}"
        for item in profile.split_sections(profile.public_excerpt(profile.read_profile(), 12000))
        if item["heading"] and item["heading"] not in cv_model.SECTIONS and item["text"]
    ]
    answers = [f"- {story['question']}: {story['answer']}" for story in application_plan.stories()]
    return "\n\n".join(rest + (["## Eerdere antwoorden\n" + "\n".join(answers)] if answers else []))


def _targets(data: dict) -> set[str]:
    ids = {"summary", *cv_model.LISTS}
    for role in data.get("roles") or []:
        ids.add(role["id"])
        ids.update(point["id"] for point in role["points"])
    ids.update(item["id"] for item in data.get("education") or [])
    return ids


def coach(mgr, data: dict) -> list[dict]:
    other = _other_sources()
    result = application_plan.call_json(
        mgr,
        f"## Cv (met id's)\n{_compact(data)}\n\n## Wat we verder over deze persoon weten\n{other or '(niets)'}\n\n"
        "Geef JSON met notes: hoogstens 12, de belangrijkste eerst. Elk {target, kind, text, suggestion}. "
        "target is precies één id uit het cv. kind is vraag, klopt_niet, sterker of niet_claimen.",
        agent="cv_coach",
        model=CoachNotes,
        ref="cv-coach",
    )
    targets = _targets(data)
    sources = _compact(data) + "\n" + other
    notes = []
    for note in result["notes"]:
        if note["kind"] not in KINDS or note["target"] not in targets or not note["text"].strip():
            continue
        if note["kind"] == "sterker":
            found = cv_model.find_point(data, note["target"])
            if not found or not note["suggestion"].strip():
                continue
            if document_content.unsupported_facts([note["suggestion"]], sources):
                continue  # a rewrite may not add numbers or names nobody gave us
        notes.append({**note, "id": cv_model.new_id("n"), "state": "open"})
    return notes[:MAX_NOTES]


def notes() -> dict:
    try:
        data = json.loads(store.meta_get(COACH_KEY) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_notes(found: list[dict], digest: str) -> None:
    store.meta_set(COACH_KEY, json.dumps({"notes": found, "digest": digest, "created_at": now_iso()}, ensure_ascii=False))


# ------------------------------------------------------------------ background tasks


def _scoped(task, work) -> dict:
    from . import auth

    user = auth._get_user_by_id(task["owner"]) if task["owner"] else None
    if user is None:
        raise RuntimeError("unknown task owner")
    with tenancy.scope({"id": user["id"], "username": user["username"], "display_name": user["display_name"], "role": user["role"]}):
        try:
            work()
        except Exception:
            log.exception("cv %s failed", task["kind"])
            _set_status("error", task["kind"].removeprefix("cv_"), error="Dit lukte niet. Probeer het opnieuw.")
    return {}


@tasks.handler("cv_import")
def _task_import(task):
    def work():
        waiting = json.loads(store.meta_get(TEXT_KEY) or "{}")
        store.meta_set(TEXT_KEY, "{}")
        if not waiting.get("text"):
            raise RuntimeError("nothing to read")
        read_back = read(get_manager(), waiting["text"])
        store.meta_set(DRAFT_KEY, json.dumps({"data": cv_model.from_read(read_back), "source": waiting.get("source", ""), "created_at": now_iso()}, ensure_ascii=False))
        _set_status("done", "import")

    return _scoped(task, work)


@tasks.handler("cv_coach")
def _task_coach(task):
    def work():
        data = cv_model.load()
        if not data:
            raise RuntimeError("no cv")
        save_notes(coach(get_manager(), data), cv_model.digest(data))
        _set_status("done", "coach")

    return _scoped(task, work)


def view(data: dict | None) -> dict:
    """The coach's open notes, placed next to what they are about."""
    found = notes()
    open_notes = [note for note in found.get("notes", []) if note.get("state") == "open"]
    by_place: dict[str, list[dict]] = {}
    for note in open_notes:
        place = note["target"]
        hit = cv_model.find_point(data or {}, place)
        if hit:
            place = hit[0]["id"]
            note = {**note, "point": hit[1]["text"]}
        elif place.startswith("e"):
            place = "education"
        by_place.setdefault(place, []).append(note)
    return {
        "by_place": by_place,
        "open": len(open_notes),
        "ran": bool(found),
        "stale": bool(found) and bool(data) and found.get("digest") != cv_model.digest(data),
    }
