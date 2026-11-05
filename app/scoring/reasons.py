"""One honest line per vacancy, and facts read from its text; free to compute per page."""

from __future__ import annotations

import json
import re

from . import wishes
from .rules import has_term, track_keywords

_CONTRACT_LABELS = {
    "vast": "Vast contract",
    "tijdelijk": "Tijdelijk",
    "freelance": "Freelance",
    "stage": "Stage of bijbaan",
}


def _first_sentence(text: str, limit: int = 150) -> str:
    sentence = re.split(r"(?<=[.!?])\s", " ".join(text.split()), maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def reason_line(job: dict, preferences: dict) -> str:
    """A verdict's own words when there is one, otherwise what the rules actually saw."""
    if job.get("match_reason"):
        try:
            why = json.loads(job["match_reason"]).get("why") or ""
        except (ValueError, AttributeError):
            why = ""
        if why:
            return _first_sentence(why)
    text = job.get("search_text") or " ".join(
        str(job.get(key) or "").lower() for key in ("title", "company", "description")
    )
    parts: list[str] = []
    for track in preferences.get("tracks", []):
        if any(has_term(keyword, text) for keyword in track_keywords(track)):
            parts.append(f"past bij {track['name'].lower()}")
            break
    _delta, notes = wishes.adjust(text, job.get("title") or "", preferences.get("wishes"))
    parts.extend(notes[:2])
    for keyword in preferences.get("keywords", {}).get("veto", []):
        if keyword and has_term(keyword, text):
            parts.append(f"bevat “{keyword}”")
            break
    return ", ".join(parts).capitalize()


def facts(job: dict) -> list[str]:
    text = job.get("search_text") or " ".join(
        str(job.get(key) or "").lower() for key in ("title", "description")
    )
    found: list[str] = []
    pay = wishes.monthly_salary(text)
    if pay:
        found.append(f"tot € {round(pay / 100) * 100:,}".replace(",", ".") + " per maand")
    if wishes.remote_kind(text):
        found.append(wishes.remote_kind(text))
    found.extend(_CONTRACT_LABELS[name] for name in sorted(wishes.contract_types(text)))
    return found[:3]
