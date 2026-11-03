"""What is still running for this account, so any page can say so."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from . import store
from .insights import STATUS_PREFIX as INSIGHT_PREFIX

STALE_SECONDS = 15 * 60
RECENT_SECONDS = 15 * 60
PREFIX = "doc_status:"
INSIGHTS = {"gap": "De vergelijking met je profiel", "prep": "Je gespreksvoorbereiding"}


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


def _current(status: dict) -> str:
    """running or done when it belongs in the overview, otherwise empty."""
    state = status.get("state")
    if state == "running":
        age = _age(status.get("updated_at"))
        return "" if age is not None and age > STALE_SECONDS else "running"
    if state == "done":
        age = _age(status.get("finished_at"))
        return "" if status.get("seen") or age is None or age > RECENT_SECONDS else "done"
    return ""


def _entries():
    """(status, job_id, what, href) for documents and for insights per vacancy."""
    for key, raw in store.meta_like(PREFIX):
        job_id = key[len(PREFIX) :]
        yield raw, job_id, None, f"/jobs/{job_id}/sollicitatie"
    for key, raw in store.meta_like(INSIGHT_PREFIX):
        kind, _sep, job_id = key[len(INSIGHT_PREFIX) :].partition(":")
        yield raw, job_id, INSIGHTS.get(kind), f"/jobs/{job_id}#inzicht"


def overview() -> dict:
    """{running: [...], done: [...]}. Done means finished lately and not looked at yet."""
    running, done = [], []
    for raw, job_id, what, href in _entries():
        try:
            status = json.loads(raw)
        except ValueError:
            continue
        current = _current(status)
        if not job_id.isdigit() or not current:
            continue
        job = store.get_job(int(job_id))
        if not job:
            continue
        what = what or _what(str(status.get("kind") or ""))
        plural = " en " in what
        item = {
            "what": what,
            "verb": ("worden gemaakt" if plural else "wordt gemaakt") if current == "running" else ("zijn klaar" if plural else "is klaar"),
            "title": job["title"] or "Vacature",
            "href": href,
        }
        (running if current == "running" else done).append(item)
    return {"running": running, "done": done}


def mark_seen(job_id: int) -> None:
    key = f"{PREFIX}{job_id}"
    try:
        status = json.loads(store.meta_get(key) or "{}")
    except ValueError:
        return
    if status.get("state") == "done" and not status.get("seen"):
        store.meta_set(key, json.dumps({**status, "seen": True}))
