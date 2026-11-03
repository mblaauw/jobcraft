"""The overview: what to do today to get closer to an interview, and what is new.

Everything here is derived from what is already stored (the application board, the
ranking, the profile): it costs no AI and is never older than the page.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from . import applications, career_position, profile, ranking, store

MAX_TASKS = 5
VISIT_KEY = "overview_last_visit"
WEEKDAYS = ("ma", "di", "wo", "do", "vr", "za", "zo")
MONTHS = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec")


def when(days: int, moment: date) -> str:
    """"vandaag", "morgen", "do 6 nov": how people talk about a deadline."""
    if days == 0:
        return "vandaag"
    if days == 1:
        return "morgen"
    return f"{WEEKDAYS[moment.weekday()]} {moment.day} {MONTHS[moment.month - 1]}"


def _who(card: dict) -> str:
    job = card["job"]
    return job["company"] or job["title"] or "deze werkgever"


def tasks(columns: list[dict], today: date | None = None) -> list[dict]:
    """At most five things, most urgent first, each with one button."""
    today = today or date.today()
    found: list[tuple[float, dict]] = []
    for column in columns:
        status = column["status"]
        for card in column["cards"]:
            job = card["job"]
            due = card.get("due")
            if status == "interview":
                found.append((100, {
                    "title": f"Bereid je gesprek bij {_who(card)} voor",
                    "why": "Ken de vragen die je kunt verwachten en weet wat je zelf wilt vragen.",
                    "href": f"/jobs/{job['id']}#inzicht", "label": "Bereid je voor", "tag": "Gesprek",
                }))
            elif status == "offer":
                found.append((95, {
                    "title": f"Beslis over het aanbod van {_who(card)}",
                    "why": "Vraag bedenktijd en leg het naast wat je zocht voordat je ja zegt.",
                    "href": "/sollicitaties", "label": "Bekijk het aanbod", "tag": "Aanbod",
                }))
            elif status == "saved":
                if due and due["days"] < 0:
                    continue
                ready = card["documents"]["cv"] and card["documents"]["letter"]
                weight = 90 - 5 * due["days"] if due and due["days"] <= 7 else (55 if ready else 45)
                moment = date.fromisoformat(due["date"]) if due else None
                found.append((weight, {
                    "title": f"{'Verstuur' if ready else 'Maak'} je sollicitatie voor {job['title'] or _who(card)}",
                    "why": (f"{_who(card)}. Je cv en brief staan klaar." if ready else f"{_who(card)}. Een cv en brief op maat duren een paar minuten."),
                    "href": f"/jobs/{job['id']}/sollicitatie", "label": "Bekijk en verstuur" if ready else "Maak je sollicitatie",
                    "tag": f"Reageer voor {when(due['days'], moment)}" if due and moment else "",
                    "urgent": bool(due and due["days"] <= 2),
                }))
            elif status == "applied" and (card.get("days_here") or 0) >= applications.FOLLOW_UP_DAYS:
                found.append((60, {
                    "title": f"Neem contact op met {_who(card)}",
                    "why": f"Je reageerde {card['days_here']} dagen geleden. Weinig mensen bellen na, dus jij valt op.",
                    "href": "/sollicitaties", "label": "Zo pak je het aan", "tag": "Nabellen",
                }))
    if profile.pending_draft():
        found.append((40, {
            "title": "Bekijk het voorstel voor je profiel",
            "why": "Je laatste antwoorden zijn verwerkt. Je ziet eerst wat er verandert.",
            "href": "/profile?tab=notities#bijwerken", "label": "Bekijk het voorstel", "tag": "",
        }))
    found.sort(key=lambda item: item[0], reverse=True)
    return [task for _weight, task in found[:MAX_TASKS]]


def week(columns: list[dict], today: date | None = None) -> dict:
    """Momentum: what went out this week, what is waiting, what led to a talk."""
    today = today or date.today()
    start = (today - timedelta(days=today.weekday())).isoformat()
    sent_week = sum(
        1
        for column in columns
        for card in column["cards"]
        if ((card.get("since") or {}).get("applied") or "") >= start
    )
    count = {column["status"]: len(column["cards"]) for column in columns}
    return {
        "sent_week": sent_week,
        "waiting": count["applied"],
        "talks": count["interview"] + count["offer"],
        **applications.funnel(columns),
    }


def last_visit() -> str | None:
    """When this account last opened the overview; the visit now becomes the new mark."""
    previous = store.meta_get(VISIT_KEY)
    store.meta_set(VISIT_KEY, datetime.now(UTC).isoformat())
    return previous


def new_matches(limit: int, since: str | None) -> tuple[list[dict], int]:
    """The best new vacancies within reach, and how many arrived since the last visit.

    Picking and counting run in SQL on the stored travel estimates, like the job list,
    so the page never walks every vacancy.
    """
    picks = store.list_jobs_page(limit, 0, "new", sort="rank", within_travel=True, only_unvoted=True)
    for item in picks:
        item["travel"] = ranking.travel_of(item)
        item["is_new"] = bool(since and (item["first_seen"] or "") > since)
        item["signals"] = ranking.signals_of(item)
    if not since:
        return picks, 0
    counts = store.job_status_counts(list_filters={"seen_after": since}, only_unvoted=True)
    total, far = counts.get("new", (0, 0))
    return picks, total - far


def tip() -> str:
    """The first of "what pays off most this week" from the position analysis, if there is one."""
    position = career_position.load() or {}
    steps = position.get("best_next_steps") or []
    return str(steps[0]) if steps else ""


def summary(task_list: list[dict], fresh: int) -> str:
    """One sentence that says what today is about."""
    parts = []
    if task_list:
        parts.append("één ding om te doen" if len(task_list) == 1 else f"{len(task_list)} dingen om te doen")
    if fresh:
        parts.append("één nieuwe vacature die past" if fresh == 1 else f"{fresh} nieuwe vacatures die passen")
    if not parts:
        return "Niets dringends vandaag. Een goed moment om nieuwe vacatures te bekijken."
    text = " en ".join(parts)
    return f"Vandaag: {text}."
