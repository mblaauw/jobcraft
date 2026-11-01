"""Application board: where each application stands, with its deadline and notes."""

from __future__ import annotations

import json
from datetime import date, timedelta

from . import store

COLUMNS = (
    ("saved", "Bewaard"),
    ("applied", "Gereageerd"),
    ("interview", "Gesprek"),
    ("offer", "Aanbod"),
    ("rejected", "Afgewezen"),
)
STATUSES = tuple(key for key, _label in COLUMNS)


def _key(job_id: int) -> str:
    return f"application:{job_id}"


def details(job_id: int) -> dict:
    try:
        return json.loads(store.meta_get(_key(job_id)) or "{}")
    except ValueError:
        return {}


def save_details(job_id: int, deadline: str, note: str) -> None:
    try:
        clean = date.fromisoformat(deadline).isoformat() if deadline else ""
    except ValueError:
        clean = ""
    store.meta_set(_key(job_id), json.dumps({"deadline": clean, "note": note.strip()[:2000]}, ensure_ascii=False))


def board() -> list[dict]:
    columns = []
    for status, label in COLUMNS:
        cards = []
        for row in store.list_jobs(None, status, include_stale=True, sort="newest"):
            cards.append({"job": row, **details(row["id"])})
        cards.sort(key=lambda card: card.get("deadline") or "9999")
        columns.append({"status": status, "label": label, "cards": cards})
    return columns


def reminders(days: int = 7, today: date | None = None) -> list[dict]:
    """Deadlines coming up for applications that are still open."""
    today = today or date.today()
    last = (today + timedelta(days=days)).isoformat()
    due = []
    for column in board():
        if column["status"] == "rejected":
            continue
        for card in column["cards"]:
            deadline = card.get("deadline")
            if deadline and deadline <= last:
                due.append({**card, "overdue": deadline < today.isoformat()})
    return sorted(due, key=lambda card: card["deadline"])
