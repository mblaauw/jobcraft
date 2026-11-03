"""Notities: the person's own notes, stored apart from the profile so no prompt can ever read them."""

from __future__ import annotations

import json

from . import store
from .util import now_iso

KEY = "private_notes"
MAX_CHARS = 50000


def load() -> dict:
    try:
        data = json.loads(store.meta_get(KEY) or "{}")
    except ValueError:
        return {"text": ""}
    return data if isinstance(data, dict) else {"text": ""}


def save(text: str) -> None:
    clean = text.replace("\r\n", "\n").strip()[:MAX_CHARS]
    store.meta_set(KEY, json.dumps({"text": clean, "updated_at": now_iso()}, ensure_ascii=False))


def append(text: str) -> None:
    current = load().get("text", "")
    save(f"{current.rstrip()}\n\n{text.strip()}".strip())
