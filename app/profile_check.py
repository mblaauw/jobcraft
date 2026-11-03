"""Warnings about the profile text itself, shown on the profile page.

The profile is what the AI reads about a person (apart from Privé and Open vragen).
A pasted document can quietly put things there that do not belong: contact details,
working notes that read as facts, or the loss of the parts matching relies on.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config, profile, store

PHONE = re.compile(r"(?<![\w.])\+?\d[\d ()\-]{8,}\d(?![\w.])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
NOTES = re.compile(
    r"\b(bron|toegevoegd|te bevestigen|waarschuwing|status|voorstel|aantekening)",
    re.IGNORECASE,
)
HIDDEN = (*profile.HIDDEN_HEADINGS, profile.PRIVATE_HEADING)


def _public(sections: list[dict]) -> list[dict]:
    return [item for item in sections if not item["heading"].startswith(HIDDEN)]


MANUAL_NOTE = "handmatige bewerking"
RESTORED_NOTE = "weggevallen onderdelen teruggezet"


def previous_version() -> str:
    """The text before the last full manual edit, so later small fixes do not hide what it dropped."""
    for row in store.list_profile_versions(10):
        if row["note"] == RESTORED_NOTE:
            return ""
        if row["note"] != MANUAL_NOTE:
            continue
        path = Path(row["path"]).resolve()
        if path.is_relative_to(config.user_path(profile.HISTORY_DIR).resolve()) and path.exists():
            return path.read_text()
        return ""
    return ""


def notes(sections: list[dict]) -> list[dict]:
    return [item for item in _public(sections) if item["heading"] and NOTES.search(item["heading"])]


def lost(markdown: str, previous: str) -> list[dict]:
    """Public parts with content in the previous version that are gone now."""
    now = {item["heading"] for item in profile.split_sections(markdown)}
    return [
        item
        for item in _public(profile.split_sections(previous))
        if item["heading"] and item["text"] and item["heading"] not in now and not NOTES.search(item["heading"])
    ]  # notes that moved under Open vragen did not fall away


def notes_apart(markdown: str) -> tuple[str, str]:
    """Working notes leave the profile: (profile without them, the notes as text for Notities)."""
    sections = profile.split_sections(markdown)
    moved = notes(sections)
    if not moved:
        return markdown, ""
    kept = [item for item in sections if item not in moved]
    block = "\n\n".join(f"### {item['heading']}\n{item['text']}" for item in moved)
    return profile.join_sections(kept), block


def lost_back(markdown: str, previous: str) -> str:
    """The parts that fell away, added back from the previous version."""
    missing = lost(markdown, previous)
    return profile.join_sections([*profile.split_sections(markdown), *missing]) if missing else markdown


def warnings(markdown: str, previous: str | None = None) -> list[dict]:
    """[{title, detail, action?}] for what in the public profile could hurt or mislead."""
    sections = profile.split_sections(markdown)
    public = _public(sections)
    found: list[dict] = []

    contact = [item["heading"] or "het begin" for item in public if PHONE.search(item["text"]) or EMAIL.search(item["text"])]
    if contact:
        found.append({
            "title": "Contactgegevens staan in je openbare profiel",
            "detail": f"Gevonden bij {', '.join(contact[:3])}. Alles buiten Privé gaat naar de AI. "
            "Je telefoon en e-mail vul je in bij je sollicitatie, onder Jouw gegevens. Haal ze hier weg of zet ze in Notities.",
        })

    found_notes = [item["heading"] for item in notes(sections)]
    if found_notes:
        found.append({
            "title": "Werkaantekeningen worden gelezen als feiten over jou",
            "detail": f"{', '.join(found_notes[:5])}. De AI gebruikt dit voor je cv en brieven, ook wat je juist niet wilt claimen. "
            "In Notities blijven ze bewaard, en daar leest de AI nooit mee.",
            "action": "/profile/aantekeningen-apart", "label": "Verplaats ze naar Notities",
        })

    before = previous if previous is not None else previous_version()
    gone = [item["heading"] for item in lost(markdown, before)] if before else []
    if gone:
        found.append({
            "title": "Bij je laatste wijziging zijn onderdelen weggevallen",
            "detail": f"{', '.join(gone[:6])}. Jobcraft gebruikt ze om vacatures te kiezen en je sollicitaties te schrijven. "
            "De rest van je tekst blijft zoals hij is.",
            "action": "/profile/onderdelen-terug", "label": "Zet ze terug",
        })
    return found
