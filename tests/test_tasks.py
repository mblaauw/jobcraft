from __future__ import annotations

import json
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
    assert tasks.latest("scan")["status"] == "error"  # not queued again: its handler may still run


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


def test_an_interrupted_scan_goes_back_in_the_queue_once():
    first = tasks.enqueue("scan", owner=None, dedupe_key="scan")
    tasks.claim_next()
    assert tasks.recover_interrupted() == 1
    old = tasks.get(first)
    assert old["status"] == "interrupted" and "opnieuw in de wachtrij" in old["error"]
    again = tasks.latest("scan", key="scan")
    assert again["id"] != first and again["status"] == "queued" and again["attempts"] == 1
    assert tasks.enqueue("scan", owner=None, dedupe_key="scan") == again["id"]  # dedupes onto it
    assert tasks.failure_kinds() == []  # an interruption is not a failure
    tasks.claim_next()
    assert tasks.recover_interrupted() == 1
    second = tasks.get(again["id"])
    assert second["status"] == "error" and "Probeer opnieuw" in second["error"]
    assert tasks.latest("scan", key="scan")["id"] == again["id"]  # no third run on its own
    assert [row["kind"] for row in tasks.failure_kinds()] == ["scan"]


def test_a_match_round_keeps_owner_and_payload_and_other_work_is_not_requeued():
    tasks.enqueue("match", owner="a", payload={"why": "scan"}, dedupe_key="match:a")
    document = tasks.enqueue("document", owner="b", payload={"job_id": 1})
    assert tasks.claim_next() and tasks.claim_next()
    assert tasks.recover_interrupted() == 2
    again = tasks.latest("match", owner="a", key="match:a")
    assert again["status"] == "queued" and again["owner"] == "a"
    assert json.loads(again["payload_json"]) == {"why": "scan"}
    assert tasks.get(document, owner="b")["status"] == "error"
    assert [row["kind"] for row in tasks.failure_kinds()] == ["document"]


def test_a_handler_cut_off_by_a_restart_requeues_its_round(monkeypatch):
    def cut_off(task):
        raise tasks.Interrupted("De scan is onderbroken door een herstart.")

    monkeypatch.setitem(tasks._HANDLERS, "scan", cut_off)
    first = tasks.enqueue("scan", owner=None, dedupe_key="scan")
    assert tasks.run_one() is True
    assert tasks.get(first)["status"] == "interrupted"
    assert tasks.latest("scan", key="scan")["status"] == "queued"
    tasks._STOP.set()
    tasks._WORKERS.append(threading.current_thread())  # as during a worker shutdown
    try:
        assert tasks.run_one() is False  # a round requeued at shutdown waits for the next start
    finally:
        tasks._STOP.clear()
        tasks._WORKERS.clear()
    assert tasks.latest("scan", key="scan")["status"] == "queued"
