"""A readable vacancy: free rules at once, and one AI read per text, shared by all accounts.

The vacancy is public text, so one reading serves everybody who opens it. Numbers the model
adds are a sign it made something up: such a reading is discarded and the rules stay in use.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from . import application_plan, config, profile, store, tasks, tenancy, vacancy_text
from .ai_budget import budget_reached
from .document_content import _numbers
from .runtime import get_manager

log = logging.getLogger("jobcraft")

FACT_LABELS = (
    ("hours", "Uren"),
    ("salary", "Salaris"),
    ("level", "Niveau"),
    ("contract", "Contract"),
    ("location", "Locatie"),
    ("deadline", "Reageer voor"),
    ("contact_person", "Contact"),
)
_TOO_SHORT = 300
READING_SHARE = 0.4
COMMON_SHARE = 0.12  # a CV word found in more vacancies than this is too ordinary to underline


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["role", "ask", "offer", "about", "apply"]
    title: str
    paragraphs: list[str]
    items: list[str]


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: str
    salary: str
    level: str
    contract: str
    location: str
    deadline: str
    contact_person: str


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    sections: list[Section]
    facts: Facts
    must_have: list[str]
    watch_out: list[str]


def _cache(content_hash: str) -> Path:
    return config.DATA_DIR / "vacancy-readings" / f"{content_hash}.json"


def load(job) -> dict | None:
    content_hash = job["content_hash"] or ""
    if not content_hash.isalnum():
        return None
    try:
        return json.loads(_cache(content_hash).read_text())
    except (OSError, ValueError):
        return None


def usable(job) -> dict | None:
    reading = load(job)
    return None if not reading or reading.get("discarded") else reading


def _save(content_hash: str, reading: dict) -> None:
    path = _cache(content_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as output:
        json.dump(reading, output, ensure_ascii=False)
    os.replace(output.name, path)


def _room_left() -> bool:
    """Tidy text may never use up the budget a CV or letter needs later that day."""
    limit = int(os.environ.get("AI_DAILY_TOKEN_LIMIT", "500000") or 0)
    return not budget_reached() and (limit <= 0 or store.daily_usage_tokens() < limit * READING_SHARE)


def worth_reading(job) -> bool:
    return bool(
        config.LLM_API_KEY
        and (job["content_hash"] or "").isalnum()
        and len(job["description"] or "") >= _TOO_SHORT
        and load(job) is None
        and _room_left()
    )


def request(job) -> bool:
    """Ask for a reading of this text. True when one is on its way."""
    if not worth_reading(job):
        return False
    tasks.enqueue("vacancy", owner=tenancy.user_id(), payload={"job_id": job["id"]}, dedupe_key=_key(job))
    return True


def _key(job) -> str:
    return f"vacancy:{tenancy.user_id()}:{job['content_hash']}"  # A key belongs to one owner.


def _asked_at(job) -> str:
    """When this reading was asked for, so the wait can show how long it has run."""
    task = tasks.latest("vacancy", owner=tenancy.user_id(), key=_key(job))
    return (task.get("created_at") or "") if task else ""


def read_now(mgr, job) -> dict | None:
    description = job["description"] or ""
    reading = application_plan.call_json(
        mgr,
        f"Titel: {job['title']}\nOrganisatie: {job['company'] or 'onbekend'}\n\n"
        f"{description[:14000]}\n\nGeef JSON met: summary, sections, facts, must_have, watch_out.",
        agent="vacancy_reader",
        model=Reading,
        ref=f"job:{job['id']}",
    )
    known = _numbers(description)
    text = json.dumps(reading, ensure_ascii=False)
    invented = _numbers(text) - known
    if invented or not reading["sections"]:
        log.warning("vacancy reading discarded: %s unknown numbers", len(invented))
        _save(job["content_hash"], {"discarded": True})  # Asking again would cost the same.
        return None
    _save(job["content_hash"], reading)
    return reading


def view(job) -> dict:
    """Everything the page shows, with what is in this person's CV marked per line."""
    excerpt = profile.read_document_excerpt(12000) if profile.profile_exists() else ""
    tracks = [track.get("name", "") for track in profile.read_preferences().get("tracks", [])]
    terms = vacancy_text.profile_terms(excerpt, tracks, lambda words: store.common_tokens(words, COMMON_SHARE))
    reading = usable(job)
    pending = worth_reading(job)
    if reading:
        sections = reading["sections"]
        facts = [(label, reading["facts"].get(key)) for key, label in FACT_LABELS if reading["facts"].get(key)]
        summary, must, watch = reading["summary"], reading["must_have"], reading["watch_out"]
    else:
        plain = vacancy_text.structure(job["description"] or "")
        sections, facts, summary, must, watch = plain["sections"], plain["facts"], "", [], []
    shown = []
    for section in sections:
        items = [
            {"pieces": vacancy_text.marked(item, terms), "in_cv": vacancy_text.in_cv(item, terms)}
            for item in section["items"]
        ]
        paragraphs = [vacancy_text.marked(item, terms) for item in section["paragraphs"]]
        shown.append(
            {
                "kind": section["kind"],
                "title": section["title"] or vacancy_text.KIND_TITLES.get(section["kind"], ""),
                "paragraphs": paragraphs,
                "items": items,
                # The plain reading knows the order of the text; the AI reading has none.
                "blocks": vacancy_text.blocks(section["order"], paragraphs, items) if section.get("order") else None,
            }
        )
    return {
        "ai": bool(reading),
        "summary": summary,
        "facts": facts,
        "sections": shown,
        "must_have": [{"text": item, "in_cv": vacancy_text.in_cv(item, terms)} for item in must],
        "watch_out": watch,
        "has_terms": bool(terms),
        "pending": pending,
        "since": _asked_at(job) if pending else "",
    }


@tasks.handler("vacancy")
def _task(task):
    from . import auth

    user = auth._get_user_by_id(task["owner"]) if task["owner"] else None
    if user is None:
        raise RuntimeError("unknown task owner")
    payload = json.loads(task["payload_json"])
    with tenancy.scope(
        {"id": user["id"], "username": user["username"], "display_name": user["display_name"], "role": user["role"]}
    ):
        job = store.get_job(int(payload["job_id"]))
        if job and worth_reading(job):
            read_now(get_manager(), dict(job))
    return {}
