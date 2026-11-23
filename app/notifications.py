"""Tell people about strong new matches: a badge in the app, a rare email for the very best."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta

from . import digest, ranking, store
from .util import now_iso

log = logging.getLogger("jobcraft")

ALERT_HOURS = 6


def _minimum(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _strong_new_jobs() -> list[dict]:
    since = store.meta_get("matches_seen_at") or ""
    minimum = _minimum("NOTIFY_MIN_RANK", "65")
    # New ones only, before the cut: with a hundred older strong vacancies still unanswered,
    # the best hundred of everything holds no fresh one and nothing was ever reported.
    rows = store.list_jobs_page(
        100, 0, "new", sort="rank", within_travel=True, only_unvoted=True,
        list_filters={"seen_after": since} if since else None,
    )
    return [row for row in rows if (row["rank_score"] or 0) >= minimum]


def refresh() -> int:
    """Runs after each assessment round. The first round only sets the starting point."""
    if not store.meta_get("matches_seen_at"):
        store.meta_set("matches_seen_at", now_iso())
    ranking.refresh_travel()
    jobs = _strong_new_jobs()
    store.meta_set("new_matches", json.dumps([job["id"] for job in jobs]))
    try:
        _alert_if_due(jobs)
    except Exception as exc:  # A mail problem must never undo finished assessments.
        log.warning("match alert failed: %s", type(exc).__name__)
    return len(jobs)


def _alert_if_due(jobs: list[dict]) -> None:
    settings = digest.settings()
    if not (settings["enabled"] and settings["email"] and digest.smtp_configured()):
        return
    alerted = set(json.loads(store.meta_get("match_alerted") or "[]"))
    best = _minimum("NOTIFY_ALERT_RANK", "80")
    fresh = [j for j in jobs if (j["rank_score"] or 0) >= best and j["id"] not in alerted]
    if not fresh:
        return
    last = store.meta_get("match_alert_at")
    if last and datetime.now(UTC) - datetime.fromisoformat(last) < timedelta(hours=ALERT_HOURS):
        return
    sent = fresh[:3]
    digest.deliver(digest.compose(sent, settings["email"]))
    store.meta_set("match_alert_at", now_iso())
    # Only what the mail named: a fourth strong vacancy gets its own mail later, not none.
    # Keep the list short: only recent ids can still show up as new.
    store.meta_set("match_alerted", json.dumps(sorted(alerted | {j["id"] for j in sent})[-500:]))
    store.count_event("match_alerts_sent")


SHOWN_KEY = "new_matches_shown"
SHOWN_FOR = timedelta(minutes=30)


def _ids(key: str) -> list:
    try:
        found = json.loads(store.meta_get(key) or "[]")
    except ValueError:
        return []
    return found if isinstance(found, list) else []


def pending() -> list[dict]:
    """The new matches; for half an hour after they were shown, the same ones again, so a
    reload or Back from one of them does not find the page empty."""
    ids = _ids("new_matches")
    try:
        shown = json.loads(store.meta_get(SHOWN_KEY) or "{}")
        if datetime.now(UTC) - datetime.fromisoformat(shown["at"]) < SHOWN_FOR:
            ids = [*ids, *(item for item in shown["ids"] if item not in ids)]
    except (ValueError, KeyError, TypeError):
        pass
    jobs = [store.get_job(job_id) for job_id in ids]
    return [dict(job) for job in jobs if job and job["status"] == "new" and not job["vote"]]


def mark_seen() -> None:
    ids = _ids("new_matches")
    if ids:
        store.meta_set(SHOWN_KEY, json.dumps({"at": now_iso(), "ids": ids}))
    store.meta_set("matches_seen_at", now_iso())
    store.meta_set("new_matches", "[]")


def badge_count() -> int:
    """For the navigation on every page; must never break a page."""
    try:
        return len(json.loads(store.meta_get("new_matches") or "[]"))
    except (ValueError, sqlite3.OperationalError):
        return 0
