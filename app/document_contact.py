"""Name and contact details for the top of a document. These never enter a prompt."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import suppress

from . import cv_text, docs, profile, store, tenancy

SETTING = "cv_contact"
SUGGESTED = "cv_contact_suggested"  # read from a CV; used until the person saves their own
FIELDS = ("name", "email", "phone", "place", "linkedin")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+|00)?\d(?:[\s().-]*\d){8,13}")
_LINKEDIN = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/\S+", re.I)


def _from_cv() -> dict:
    """What we can read from the header of someone's own Word CV."""
    try:
        identity = docs.cv_identity()
    except (OSError, ValueError, KeyError):
        return {}
    found = {"name": (identity.get("name") or [""])[0]}
    lines = " | ".join(identity.get("contact") or [])
    for key, pattern in (("email", _EMAIL), ("linkedin", _LINKEDIN), ("phone", _PHONE)):
        match = pattern.search(lines)
        if match:
            found[key] = match.group(0).strip()
    return found


def suggest(found: dict) -> None:
    """Remember how a CV says to reach someone. It never replaces what the person saved."""
    clean = {key: " ".join(str(found.get(key) or "").split())[:160] for key in FIELDS if found.get(key)}
    if clean:
        store.meta_set(SUGGESTED, json.dumps(clean, ensure_ascii=False))


def _suggested() -> dict:
    with suppress(ValueError):
        saved = json.loads(store.meta_get(SUGGESTED) or "{}")
        if saved:
            return saved
    # Older accounts pasted their CV into the profile before suggestions were kept.
    return cv_text.contact_of(profile.read_profile()) if profile.profile_exists() else {}


def adopt(found: dict) -> None:
    """Save what was found, keeping every field the person already filled in."""
    current = load()
    merged = {key: current[key] or str(found.get(key) or "") for key in FIELDS}
    if found.get("name") and len(current["name"].split()) < 2:  # a username is not a name
        merged["name"] = found["name"]
    save(merged)


def load() -> dict:
    saved = {}
    with suppress(ValueError):
        saved = json.loads(store.meta_get(SETTING) or "{}")
    suggested, from_word = _suggested(), _from_cv()
    known = {**suggested, **{key: value for key, value in from_word.items() if value}}
    home = profile.read_preferences().get("home", {}).get("label", "")
    # A username often ends up as "name"; the first one that is a full name wins.
    names = [str(item or "") for item in (saved.get("name"), from_word.get("name"), suggested.get("name"))]
    name = next((item for item in names if len(item.split()) >= 2), next((item for item in names if item), ""))
    contact = {
        "name": name or tenancy.display_name(),
        "email": saved.get("email", known.get("email") or store.meta_get("digest_email") or ""),
        "phone": saved.get("phone", known.get("phone", "")),
        "place": saved.get("place", "" if home == "Nog niet ingesteld" else home),
        "linkedin": saved.get("linkedin", known.get("linkedin", "")),
    }
    return {key: str(contact.get(key) or "") for key in FIELDS}


def save(values: dict[str, str]) -> None:
    clean = {key: " ".join(str(values.get(key) or "").split())[:160] for key in FIELDS}
    store.meta_set(SETTING, json.dumps(clean, ensure_ascii=False))


def confirmed() -> bool:
    return bool(store.meta_get(SETTING))


def from_cv_unconfirmed(contact: dict) -> bool:
    """Shown with a request to check: details taken from the CV that nobody saved yet."""
    return not confirmed() and any(contact.get(key) for key in ("email", "phone")) and not missing(contact)


def missing(contact: dict) -> bool:
    """An employer must be able to reach someone, and needs a real name to call them by."""
    return not (contact.get("email") or contact.get("phone")) or len(contact.get("name", "").split()) < 2


def key(contact: dict) -> str:
    """Which details a document carries, to notice when they have changed since."""
    raw = json.dumps({field: contact.get(field, "") for field in FIELDS}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
