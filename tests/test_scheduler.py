from __future__ import annotations

from datetime import UTC, datetime

from app import scheduler, store, tasks


def test_due_slot_fires_once_per_configured_time():
    now = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
    assert scheduler.due_slot(now, "07:30,17:30", None) == "2026-10-20T07:30:00+00:00"
    assert scheduler.due_slot(now, "07:30,17:30", "2026-10-20T07:30:00+00:00") is None
    assert scheduler.due_slot(now, "", None) is None


def test_run_due_enqueues_shared_scan(monkeypatch):
    monkeypatch.setenv("SCAN_TIMES", "07:30")
    store.meta_set("scheduler_last_fire", "")
    due = scheduler.run_due(datetime(2026, 10, 20, 8, 0, tzinfo=UTC))
    assert due == "2026-10-20T07:30:00+00:00"
    assert tasks.latest("scan")["status"] == "queued"
    assert scheduler.run_due(datetime(2026, 10, 20, 8, 1, tzinfo=UTC)) is None
