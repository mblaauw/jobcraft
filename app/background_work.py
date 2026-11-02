"""What is still running for this account, so any page can say so."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from . import store

STALE_SECONDS = 15 * 60
RECENT_SECONDS = 15 * 60
PREFIX = "doc_status:"


def _age(stamp: str | None) -> float | None:
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(stamp or "")).total_seconds()
    except ValueError:
        return None


def _what(kind: str) -> str:
    if kind == "plan":
        return "Je sollicitatieplan"
    if kind == "resume":
        return "Je cv"
    if kind == "motivatie":
        return "Je motivatiebrief"
    if kind.startswith("tone:"):
        return "Je nieuwe versie"
    return "Je cv en brief"


def overview() -> dict:
    """{running: [...], done: [...]}. Done means finished lately and not looked at yet."""
    running, done = [], []
    for key, raw in store.meta_like(PREFIX):
        try:
            status = json.loads(raw)
        except ValueError:
            continue
        job_id = key[len(PREFIX) :]
        if not job_id.isdigit():
            continue
        state = status.get("state")
        if state == "running":
            age = _age(status.get("updated_at"))
            if age is not None and age > STALE_SECONDS:
                continue
        elif state == "done":
            age = _age(status.get("finished_at"))
            if status.get("seen") or age is None or age > RECENT_SECONDS:
                continue
        else:
            continue
        job = store.get_job(int(job_id))
        if not job:
            continue
        what = _what(str(status.get("kind") or ""))
        item = {
            "what": what,
            "verb": ("worden gemaakt" if " en " in what else "wordt gemaakt") if state == "running" else ("zijn klaar" if " en " in what else "is klaar"),
            "title": job["title"] or "Vacature",
            "href": f"/jobs/{job_id}/sollicitatie",
        }
        (running if state == "running" else done).append(item)
    return {"running": running, "done": done}


def mark_seen(job_id: int) -> None:
    key = f"{PREFIX}{job_id}"
    try:
        status = json.loads(store.meta_get(key) or "{}")
    except ValueError:
        return
    if status.get("state") == "done" and not status.get("seen"):
        store.meta_set(key, json.dumps({**status, "seen": True}))
