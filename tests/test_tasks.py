from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app import tasks


def test_task_enqueue_dedupes_and_survives_completion():
    first = tasks.enqueue("match", owner="a", dedupe_key="match:a")
    second = tasks.enqueue("match", owner="a", dedupe_key="match:a")
    assert first == second
    task = tasks.claim_next()
    assert task and task["id"] == first and task["status"] == "running"
    tasks.finish(first, {"matched": 1})
    third = tasks.enqueue("match", owner="a", dedupe_key="match:a")
    assert third != first
    assert tasks.latest("match", owner="a")["id"] == third


def test_one_llm_task_per_owner_but_other_owners_run():
    first = tasks.enqueue("document", owner="a", dedupe_key="doc:a")
    second = tasks.enqueue("document", owner="b", dedupe_key="doc:b")
    claimed = tasks.claim_next()
    assert claimed["id"] == first
    tasks.enqueue("match", owner="a", dedupe_key="match:a")
    blocked = tasks.claim_next()
    assert blocked["id"] == second


def test_worker_dispatches_handler_and_records_failure():
    seen = []

    @tasks.handler("fixture")
    def fixture(task):
        seen.append(task["id"])
        return {"ok": True}

    task_id = tasks.enqueue("fixture", owner=None)
    assert tasks.run_one() is True
    assert seen == [task_id]
    assert tasks.latest("fixture")["status"] == "done"

    @tasks.handler("broken")
    def broken(task):
        raise RuntimeError("boom")

    failed = tasks.enqueue("broken", owner=None)
    assert tasks.run_one() is True
    assert tasks.latest("broken")["status"] == "error"
    assert "boom" in tasks.latest("broken")["error"]
    assert failed


def test_fail_and_stale_recovery():
    task_id = tasks.enqueue("scan", owner=None)
    claimed = tasks.claim_next()
    assert claimed["id"] == task_id
    tasks.fail(task_id, "provider down")
    assert tasks.latest("scan")["status"] == "error"

    stale = tasks.enqueue("scan", owner=None)
    tasks.claim_next()
    with tasks.store.connect() as con:
        con.execute(
            "UPDATE tasks SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (stale,)
        )
    assert tasks.recover_stale() == 1
    assert tasks.latest("scan")["status"] == "error"


def test_user_source_preferences_are_owner_scoped():
    from app import store

    store.set_user_source("fixture", False)
    assert store.list_user_sources() == {"fixture": False}
    store.set_user_source("fixture", True)
    assert store.list_user_sources() == {"fixture": True}


def _race_old_select(monkeypatch, prefix, atomic_prefix):
    """Contend atomic writes; force the bad interleaving if SELECT/write returns."""
    original = tasks.store.connect
    selected = threading.Barrier(2)

    class Connection:
        def __init__(self):
            self.connection = original()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            try:
                return self.connection.__exit__(*args)
            finally:
                self.connection.close()

        def execute(self, sql, *args):
            if sql.startswith(atomic_prefix):
                selected.wait(timeout=5)
            cursor = self.connection.execute(sql, *args)
            if sql.startswith(prefix):
                row = cursor.fetchone()
                selected.wait(timeout=5)
                return SimpleNamespace(fetchone=lambda: row)
            return cursor

    monkeypatch.setattr(tasks.store, "connect", Connection)


def test_concurrent_enqueue_returns_same_active_id(monkeypatch):
    _race_old_select(monkeypatch, "SELECT id FROM tasks WHERE dedupe_key", "INSERT INTO tasks")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: tasks.enqueue("match", owner="a", dedupe_key="match:a"), range(2))
        )
    assert results[0] == results[1]


def test_concurrent_claim_cannot_run_one_task_twice(monkeypatch):
    task_id = tasks.enqueue("document", owner="a")
    _race_old_select(monkeypatch, "SELECT * FROM tasks t", "UPDATE tasks SET status='running'")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: tasks.claim_next(), range(2)))
    assert [row["id"] for row in claimed if row] == [task_id]
    task = tasks.get(task_id, owner="a")
    assert task["attempts"] == 1
    assert task["started_at"] and task["heartbeat_at"]
    assert next(row for row in claimed if row) == task


def test_keyed_latest_is_owner_scoped():
    task_id = tasks.enqueue("document", owner="a", dedupe_key="document:a:1")
    assert tasks.latest("document", owner="a", key="document:a:1")["id"] == task_id
    assert tasks.latest("document", owner="b", key="document:a:1") is None
    assert tasks.latest("document", key="document:a:1") is None


def test_dedupe_key_cannot_return_another_owners_task():
    task_id = tasks.enqueue("match", owner="a", dedupe_key="collision")
    with pytest.raises(ValueError, match="owner"):
        tasks.enqueue("match", owner="b", dedupe_key="collision")
    assert tasks.get(task_id, owner="a")["status"] == "queued"


def test_concurrent_claim_obeys_per_owner_and_shared_scan_exclusion():
    for owner in ("a", "b"):
        tasks.enqueue("document", owner=owner)
        tasks.enqueue("match", owner=owner)
    tasks.enqueue("scan", owner=None)
    tasks.enqueue("scan", owner=None)
    start = threading.Barrier(6)

    def claim(_):
        start.wait(timeout=5)
        return tasks.claim_next()

    with ThreadPoolExecutor(max_workers=6) as pool:
        claimed = [row for row in pool.map(claim, range(6)) if row]
    assert len(claimed) == 3
    assert {row["owner"] for row in claimed} == {"a", "b", None}
    assert len({row["id"] for row in claimed}) == 3
