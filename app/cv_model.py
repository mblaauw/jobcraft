"""Mijn cv: the person's CV as structured parts, and the profile text made from it.

The CV is stored per account as JSON. Every point remembers where it came from (the
imported CV, the person, an answer to the coach, a coach rewrite they accepted).
Saving writes the CV sections of the profile text, so matching, CVs and letters keep
reading the profile exactly as before; the other profile sections stay untouched.
"""

from __future__ import annotations

import hashlib
import json
import secrets

from pydantic import BaseModel, ConfigDict, Field

from . import cv_text, profile, store
from .util import now_iso

KEY = "cv_data"
PREVIOUS_KEY = "cv_previous"
SECTIONS = ("Over mij", "Werkervaring", "Opleiding", "Vaardigheden", "Talen", "Aanvullend", "Interesses")
LISTS = {"skills": "Vaardigheden", "languages": "Talen", "extras": "Aanvullend", "interests": "Interesses"}
SOURCES = {"cv": "Uit je cv", "eigen": "Door jou", "antwoord": "Jouw antwoord", "coach": "Herschreven"}


class ReadRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    organisation: str = ""
    place: str = ""
    period: str = ""
    points: list[str] = Field(default_factory=list)


class ReadEducation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    school: str = ""
    period: str = ""


class ReadCv(BaseModel):
    """What the AI returns when it reads a CV; ids and sources are added afterwards."""

    model_config = ConfigDict(extra="forbid")
    summary: str = ""
    roles: list[ReadRole] = Field(default_factory=list)
    education: list[ReadEducation] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    extras: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)


def new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(3)}"


def _clean(items) -> list[str]:
    return [str(item).strip() for item in items or [] if str(item).strip()]


def from_read(read: dict, source: str = "cv") -> dict:
    """The AI's reading, with an id on everything and a source on every point."""
    return {
        "summary": str(read.get("summary") or "").strip(),
        "roles": [
            {
                "id": new_id("r"),
                **{key: str(role.get(key) or "").strip() for key in ("title", "organisation", "place", "period")},
                "points": [{"id": new_id("p"), "text": text, "source": source} for text in _clean(role.get("points"))],
            }
            for role in read.get("roles") or []
            if str(role.get("title") or "").strip()
        ],
        "education": [
            {"id": new_id("e"), **{key: str(item.get(key) or "").strip() for key in ("title", "school", "period")}}
            for item in read.get("education") or []
            if str(item.get("title") or "").strip()
        ],
        **{key: _clean(read.get(key)) for key in LISTS},
    }


def load() -> dict | None:
    try:
        data = json.loads(store.meta_get(KEY) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def digest(data: dict | None) -> str:
    content = {key: value for key, value in (data or {}).items() if key != "updated_at"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def role_line(role: dict) -> str:
    where = ", ".join(part for part in (role.get("organisation"), role.get("place")) if part)
    head = " — ".join(part for part in (role.get("period"), role.get("title")) if part)
    return f"{head} | {where}" if where else head


def sections(data: dict) -> dict[str, str]:
    """The profile text for each CV section, in the markdown the rest of the app reads."""
    work = "\n\n".join(
        f"### {role_line(role)}\n" + "\n".join(f"- {point['text']}" for point in role["points"])
        for role in data.get("roles") or []
    )
    education = "\n".join(
        "- " + " — ".join(part for part in (item["title"], ", ".join(p for p in (item.get("school"), item.get("period")) if p)) if part)
        for item in data.get("education") or []
    )
    texts = {"Over mij": data.get("summary") or "", "Werkervaring": work, "Opleiding": education}
    for key, heading in LISTS.items():
        texts[heading] = "\n".join(f"- {item}" for item in data.get(key) or [])
    return {heading: texts[heading].strip() for heading in SECTIONS}


def save(data: dict, note: str) -> None:
    """Store the CV and write its sections into the profile text as a new version."""
    data = {**data, "updated_at": now_iso()}
    previous = load()
    if previous and digest(previous) != digest(data):
        store.meta_set(PREVIOUS_KEY, json.dumps(previous, ensure_ascii=False))
    store.meta_set(KEY, json.dumps(data, ensure_ascii=False))
    profile.merge_sections(sections(data), note=note)


def find_role(data: dict, role_id: str) -> dict | None:
    return next((role for role in data.get("roles") or [] if role["id"] == role_id), None)


def find_point(data: dict, point_id: str) -> tuple[dict, dict] | None:
    for role in data.get("roles") or []:
        for point in role["points"]:
            if point["id"] == point_id:
                return role, point
    return None


def points_from_lines(old: list[dict], text: str) -> list[dict]:
    """Edited lines keep their source when unchanged; new or changed lines are the person's own."""
    known = {point["text"]: point for point in old}
    result = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip().lstrip("-•* ").strip()
        if not line:
            continue
        kept = known.get(line)
        result.append(kept if kept else {"id": new_id("p"), "text": line, "source": "eigen"})
    return result


def lines(text: str) -> list[str]:
    return [line.strip().lstrip("-•* ").strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]


def without_contact(data: dict) -> dict:
    """The CV without phone numbers, e-mail or LinkedIn in its text; those belong in Jouw gegevens."""
    clean = json.loads(json.dumps(data))
    clean["summary"] = cv_text.strip_contact(clean.get("summary") or "")
    for role in clean.get("roles") or []:
        for point in role["points"]:
            point["text"] = cv_text.strip_contact(point["text"])
        role["points"] = [point for point in role["points"] if point["text"]]
    for key in LISTS:
        clean[key] = [text for text in (cv_text.strip_contact(item) for item in clean.get(key) or []) if text]
    return clean
