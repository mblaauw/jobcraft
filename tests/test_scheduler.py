from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import scheduler, store, tasks


def test_due_slot_fires_once_per_configured_time():
    now = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
    assert scheduler.due_slot(now, "07:30,17:30", None) == "2026-10-20T05:30:00+00:00"
    assert scheduler.due_slot(now, "07:30,17:30", "2026-10-20T05:30:00+00:00") is None
    assert scheduler.due_slot(now, "", None) is None


def test_run_due_enqueues_shared_scan(monkeypatch):
    monkeypatch.setenv("SCAN_TIMES", "07:30")
    store.meta_set("scheduler_last_fire", "")
    due = scheduler.run_due(datetime(2026, 10, 20, 8, 0, tzinfo=UTC))
    assert due == "2026-10-20T05:30:00+00:00"
    assert tasks.latest("scan")["status"] == "queued"
    assert scheduler.run_due(datetime(2026, 10, 20, 8, 1, tzinfo=UTC)) is None


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-07-01T05:29:00+00:00", None),
        ("2026-07-01T05:30:00+00:00", "2026-07-01T05:30:00+00:00"),
        ("2026-12-01T06:29:00+00:00", None),
        ("2026-12-01T06:30:00+00:00", "2026-12-01T06:30:00+00:00"),
    ],
)
def test_schedule_uses_amsterdam_wall_clock_in_summer_and_winter(now, expected):
    previous = "2026-06-30T05:30:00+00:00" if "07-01" in now else "2026-11-30T06:30:00+00:00"
    assert scheduler.due_slot(datetime.fromisoformat(now), "07:30", previous) == expected


def test_late_restart_only_fires_latest_slot_and_does_not_replay(monkeypatch):
    monkeypatch.setenv("SCAN_TIMES", "07:30,17:30")
    now = datetime(2026, 10, 20, 20, tzinfo=UTC)
    assert scheduler.run_due(now) == "2026-10-20T15:30:00+00:00"
    first = tasks.latest("scan")
    tasks.claim_next()
    tasks.finish(first["id"])
    assert scheduler.run_due(now) is None
    assert tasks.latest("scan")["id"] == first["id"]


def test_slot_order_compares_instants_not_offset_strings():
    now = datetime(2026, 10, 20, 8, tzinfo=UTC)
    assert scheduler.due_slot(now, "07:30", "2026-10-20T07:30:00+02:00") is None


def test_malformed_or_out_of_range_slots_are_ignored():
    assert scheduler.slots("25:00,-1:00,09:90,nope,07:30,07:30") == [(7, 30)]


def test_ambiguous_autumn_slot_fires_only_once():
    first = "2026-10-25T00:30:00+00:00"  # first local 02:30
    assert scheduler.due_slot(datetime.fromisoformat(first), "02:30", None) == first
    second = datetime.fromisoformat("2026-10-25T01:45:00+00:00")
    assert scheduler.due_slot(second, "02:30", first) is None


def test_restart_after_midnight_catches_up_yesterdays_latest_slot():
    now = datetime.fromisoformat("2026-10-20T23:00:00+00:00")  # 01:00 Amsterdam
    last = "2026-10-20T05:30:00+00:00"
    due = scheduler.due_slot(now, "07:30,17:30", last)
    assert due == "2026-10-20T15:30:00+00:00"
    assert scheduler.due_slot(now, "07:30,17:30", due) is None


def test_shutdown_waits_for_the_scheduler_before_releasing_its_thread(monkeypatch):
    joined = []

    class SchedulerThread:
        def join(self, timeout=None):
            joined.append(timeout)

    monkeypatch.setattr(scheduler, "_THREAD", SchedulerThread())
    scheduler.stop()
    assert joined == [None], "a timed join can release the process lock while a tick still runs"
    assert scheduler._THREAD is None
    scheduler.stop()  # repeated cleanup must not join the previous thread again
    assert joined == [None]
