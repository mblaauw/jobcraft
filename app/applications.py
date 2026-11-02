"""Application board: where each application stands, with its deadline and notes."""

from __future__ import annotations

import json
from datetime import date, timedelta

from . import store, vacancy_text

COLUMNS = (
    ("saved", "Bewaard"),
    ("applied", "Gereageerd"),
    ("interview", "Gesprek"),
    ("offer", "Aanbod"),
    ("rejected", "Afgewezen"),
)
STATUSES = tuple(key for key, _label in COLUMNS)
FOLLOW_UP_DAYS = 7
HINTS = {
    "saved": "Nog te versturen",
    "applied": "Verstuurd, wacht op antwoord",
    "interview": "Hier draait het om",
    "offer": "Bijna binnen",
    "rejected": "Hier leer je van",
}


def _key(job_id: int) -> str:
    return f"application:{job_id}"


def details(job_id: int) -> dict:
    try:
        return json.loads(store.meta_get(_key(job_id)) or "{}")
    except ValueError:
        return {}


def save_details(job_id: int, deadline: str, note: str, status: str = "") -> None:
    try:
        clean = date.fromisoformat(deadline).isoformat() if deadline else ""
    except ValueError:
        clean = ""
    current = details(job_id)
    since = current.get("since") or {}
    if status and status != current.get("status"):
        since[status] = date.today().isoformat()
    store.meta_set(
        _key(job_id),
        json.dumps(
            {"deadline": clean, "note": note.strip()[:2000], "status": status or current.get("status", ""), "since": since},
            ensure_ascii=False,
        ),
    )


def _documents(job_id: int) -> dict:
    kinds = {row["kind"] for row in store.list_documents(job_id)}
    return {"cv": "resume" in kinds, "letter": "motivatie" in kinds}


def _days_since(card: dict, status: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat((card.get("since") or {}).get(status, ""))).days
    except ValueError:
        return None


def next_step(status: str, card: dict, today: date | None = None) -> dict:
    """The one thing that most raises the odds for this application right now."""
    today = today or date.today()
    job_id = card["job"]["id"]
    studio = f"/jobs/{job_id}/sollicitatie"
    days = _days_since(card, status, today)
    if status == "saved":
        if not (card["documents"]["cv"] and card["documents"]["letter"]):
            return {"text": "Maak je cv en brief op maat", "href": studio, "label": "Maak je sollicitatie", "urgent": True}
        return {"text": "Alles staat klaar. Verstuur het, en zet deze vacature daarna op Gereageerd.", "href": studio, "label": "Bekijk en verstuur", "urgent": True}
    if status == "applied":
        if days is not None and days >= FOLLOW_UP_DAYS:
            return {"text": f"Je reageerde {days} dagen geleden. Bellen of mailen valt op en helpt: weinig sollicitanten doen het.", "follow_up": True, "urgent": True}
        return {"text": "Wacht op antwoord. Na een week kun je zelf contact opnemen, dat vergroot je kans.", "follow_up": True, "urgent": False}
    if status == "interview":
        return {"text": "Bereid je gesprek voor: de vragen die je kunt verwachten en wat je terugvraagt.", "href": f"/jobs/{job_id}#inzicht", "label": "Bereid je voor", "urgent": True}
    if status == "offer":
        return {"text": "Reageer nog niet meteen. Vraag bedenktijd, en vergelijk het aanbod met wat je zocht.", "urgent": True}
    return {"text": "Vraag om feedback: dat is de snelste manier om je volgende sollicitatie sterker te maken.", "urgent": False}


def _deadline(card: dict, today: date) -> dict | None:
    try:
        moment = date.fromisoformat(card.get("deadline") or "")
    except ValueError:
        return None
    days = (moment - today).days
    return {"date": moment.isoformat(), "days": days}


def board() -> list[dict]:
    today = date.today()
    columns = []
    for status, label in COLUMNS:
        cards = []
        for row in store.list_jobs(None, status, include_stale=True, sort="newest"):
            card = {"job": row, **details(row["id"]), "documents": _documents(row["id"])}
            card["next"] = next_step(status, card, today)
            card["due"] = _deadline(card, today)
            card["days_here"] = _days_since(card, status, today)
            card["apply_by"] = dict(vacancy_text.facts_of(row["description"] or "")).get("Reageer voor", "")
            cards.append(card)
        cards.sort(key=lambda card: card.get("deadline") or "9999")
        columns.append({"status": status, "label": label, "hint": HINTS[status], "cards": cards})
    return columns


def funnel(columns: list[dict]) -> dict:
    """Honest numbers: how many got further, only once there is something to say."""
    count = {column["status"]: len(column["cards"]) for column in columns}
    sent = count["applied"] + count["interview"] + count["offer"] + count["rejected"]
    talks = count["interview"] + count["offer"]
    return {"sent": sent, "talks": talks, "rate": round(100 * talks / sent) if sent >= 5 else None}


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
