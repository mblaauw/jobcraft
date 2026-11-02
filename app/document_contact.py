"""Name and contact details for the top of a document. These never enter a prompt."""

from __future__ import annotations

import json
import re
from contextlib import suppress

from . import docs, profile, store, tenancy

SETTING = "cv_contact"
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


def load() -> dict:
    saved = {}
    with suppress(ValueError):
        saved = json.loads(store.meta_get(SETTING) or "{}")
    known = _from_cv()
    home = profile.read_preferences().get("home", {}).get("label", "")
    contact = {
        "name": saved.get("name") or known.get("name") or tenancy.display_name(),
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


def missing(contact: dict) -> bool:
    """An employer must be able to reach someone, and needs a real name to call them by."""
    return not (contact.get("email") or contact.get("phone")) or len(contact.get("name", "").split()) < 2
