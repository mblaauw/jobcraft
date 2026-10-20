"""Minimal in-process scheduler for shared scans."""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime

from . import store, tasks

_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def slots(raw: str) -> list[tuple[int, int]]:
    result = []
    for item in raw.split(","):
        try:
            hour, minute = item.strip().split(":")
            result.append((int(hour), int(minute)))
        except ValueError:
            continue
    return sorted(result)


def due_slot(now: datetime, raw: str, last: str | None) -> str | None:
    """Return the latest due ISO slot that has not fired yet."""
    for hour, minute in slots(raw):
        candidate = now.astimezone(UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now and (not last or candidate.isoformat() > last):
            return candidate.isoformat()
    return None


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
    _STOP.set()
    if _THREAD:
        _THREAD.join(timeout=5)
