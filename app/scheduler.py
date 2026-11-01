"""Minimal in-process scheduler for shared scans."""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import digest, store, tasks

_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def slots(raw: str) -> list[tuple[int, int]]:
    result = set()
    for item in raw.split(","):
        try:
            hour, minute = item.strip().split(":")
            hour, minute = int(hour), int(minute)
            if 0 <= hour < 24 and 0 <= minute < 60:
                result.add((hour, minute))
        except ValueError:
            continue
    return sorted(result)


def due_slot(now: datetime, raw: str, last: str | None) -> str | None:
    """Use Amsterdam wall times and persist UTC instants, latest slot only.

    An ambiguous autumn time fires on its first occurrence, never twice.
    """
    local = now.astimezone(ZoneInfo("Europe/Amsterdam"))
    last_instant = datetime.fromisoformat(last).astimezone(UTC) if last else None
    candidates = [
        (local - timedelta(days=days))
        .replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
        .astimezone(UTC)
        for days in (0, 1)
        for hour, minute in slots(raw)
    ]
    due = [candidate for candidate in candidates if candidate <= now]
    latest = max(due) if due else None
    return (
        latest.isoformat() if latest and (last_instant is None or latest > last_instant) else None
    )


def _schedule_times() -> str:
    return store.meta_get("scan_schedule_times", os.environ.get("SCAN_TIMES", "07:30,17:30")) or ""


def _schedule_enabled() -> bool:
    value = store.meta_get("scan_schedule_enabled")
    if value is None:
        return bool(os.environ.get("SCAN_TIMES", "07:30,17:30").strip())
    return value == "1"


def run_due(now: datetime | None = None) -> str | None:
    if not _schedule_enabled():
        return None
    now = now or datetime.now(UTC)
    raw = _schedule_times()
    if not raw.strip():
        return None
    due = due_slot(now, raw, store.meta_get("scheduler_last_fire"))
    if due:
        tasks.enqueue("scan", owner=None, dedupe_key="scan")
        store.meta_set("scheduler_last_fire", due)
    return due


def run_digest_due(now: datetime | None = None) -> bool:
    """Once a day after DIGEST_TIME; each task decides by itself whether its owner opted in."""
    if not digest.smtp_configured():
        return False
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo("Europe/Amsterdam"))
    moments = slots(os.environ.get("DIGEST_TIME", "08:30"))
    today = local.date().isoformat()
    if not moments or (local.hour, local.minute) < moments[0]:
        return False
    if store.meta_get("digest_last_tick") == today:
        return False
    store.meta_set("digest_last_tick", today)
    for owner in store.active_owners_with_profile():
        tasks.enqueue("digest", owner=owner, dedupe_key=f"digest:{owner}")
    return True


def start() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()

    def loop():
        while not _STOP.wait(60):
            try:
                run_due()
                run_digest_due()
            except Exception:
                continue

    _THREAD = threading.Thread(target=loop, daemon=True)
    _THREAD.start()


def stop() -> None:
    """Finish an in-flight tick before the lifespan releases its process lock."""
    global _THREAD
    _STOP.set()
    if _THREAD:
        _THREAD.join()
        _THREAD = None
