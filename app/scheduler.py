"""Minimal in-process scheduler for shared scans."""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import store, tasks

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


def run_due(now: datetime | None = None) -> str | None:
    now = now or datetime.now(UTC)
    raw = os.environ.get("SCAN_TIMES", "07:30,17:30")
    due = due_slot(now, raw, store.meta_get("scheduler_last_fire"))
    if due:
        tasks.enqueue("scan", owner=None, dedupe_key="scan")
        store.meta_set("scheduler_last_fire", due)
    return due


def start() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()

    def loop():
        while not _STOP.wait(60):
            try:
                run_due()
            except Exception:  # noqa: BLE001 - scheduler must not stop the app
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
