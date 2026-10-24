"""Owner scope, process exclusivity and shutdown use only synthetic state."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app import config, main, profile, runtime, store, tasks, tenancy

A = {"id": "a", "username": "a", "display_name": "Account A", "role": "user"}
B = {"id": "b", "username": "b", "display_name": "Account B", "role": "user"}


def test_worker_uses_persisted_owner_and_restores_callers_scope(monkeypatch):
    for owner in (A, B):
        with tenancy.scope(owner):
            profile.write_profile(f"## Werk\nOnly {owner['id']}", "fixture")
    observed = []

    def handler(task):
        observed.append((tenancy.user_id(), profile.read_profile()))
        return {"owner": tenancy.user_id()}

    monkeypatch.setitem(tasks._HANDLERS, "scoped-fixture", handler)
    task_id = tasks.enqueue("scoped-fixture", owner="a")
    with tenancy.scope(B):
        assert tasks.run_one(resolve_owner=lambda owner: A if owner == "a" else None)
        assert tenancy.current()["id"] == "b"
    assert observed == [("a", "## Werk\nOnly a\n")]
    assert json.loads(tasks.get(task_id, owner="a")["result_json"]) == {"owner": "a"}
    assert tasks.get(task_id, owner="b") is None


def test_shared_task_explicitly_clears_ambient_owner(monkeypatch):
    observed = []
    monkeypatch.setitem(
        tasks._HANDLERS, "shared-fixture", lambda task: observed.append(tenancy.current())
    )
    tasks.enqueue("shared-fixture", owner=None)
    with tenancy.scope(B):
        assert tasks.run_one()
        assert tenancy.current()["id"] == "b"
    assert observed == [None]


@pytest.mark.parametrize("resolve_owner", [None, lambda owner: None])
def test_owner_tasks_fail_closed_when_owner_cannot_be_resolved(monkeypatch, resolve_owner):
    observed = []
    monkeypatch.setitem(tasks._HANDLERS, "owned-fixture", lambda task: observed.append(task))
    task_id = tasks.enqueue("owned-fixture", owner="missing")
    assert tasks.run_one(resolve_owner=resolve_owner)
    assert observed == []
    assert tasks.get(task_id, owner="missing")["status"] == "error"


def test_task_owner_resolver_rejects_inactive_accounts(monkeypatch):
    monkeypatch.setattr(main.auth, "_get_user_by_id", lambda owner: {**A, "active": False})
    assert main._resolve_task_owner("a") is None


def test_exclusive_data_dir_lock_is_not_unlinked_and_is_released(tmp_path):
    from app.runtime_lock import exclusive_data_dir

    with exclusive_data_dir(tmp_path):
        with pytest.raises(RuntimeError, match="already running"):
            with exclusive_data_dir(tmp_path):
                pytest.fail("second process lock was accepted")
    lock = tmp_path / ".jobcraft.lock"
    assert lock.exists()
    with exclusive_data_dir(tmp_path):
        pass


def test_lock_is_released_after_startup_exception(monkeypatch):
    import asyncio

    from app.runtime_lock import exclusive_data_dir

    def fail():
        raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(store, "init_db", fail)

    async def start():
        async with main.lifespan(main.app):
            pytest.fail("startup unexpectedly succeeded")

    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        asyncio.run(start())
    with exclusive_data_dir(config.DATA_DIR):
        pass


def test_restart_recovers_even_recent_running_tasks_but_preserves_queue():
    running = tasks.enqueue("scan", owner=None, dedupe_key="scan")
    tasks.claim_next()
    queued = tasks.enqueue("match", owner="a")
    assert tasks.recover_interrupted() == 1
    assert tasks.get(running)["status"] == "error"
    assert tasks.get(queued, owner="a")["status"] == "queued"
    assert tasks.enqueue("scan", owner=None, dedupe_key="scan") != running


def test_heartbeat_continues_until_active_task_finishes_during_shutdown(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    beat = threading.Event()

    def handler(task):
        entered.set()
        assert release.wait(timeout=5)
        return {"finished": True}

    original_heartbeat = tasks.heartbeat

    def heartbeat(task_id):
        original_heartbeat(task_id)
        beat.set()

    monkeypatch.setitem(tasks._HANDLERS, "blocking-fixture", handler)
    monkeypatch.setattr(tasks, "heartbeat", heartbeat)
    monkeypatch.setattr(tasks, "HEARTBEAT_INTERVAL", 0.01)
    task_id = tasks.enqueue("blocking-fixture", owner=None)
    tasks.start_workers(1)
    try:
        assert entered.wait(timeout=2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            stopping = pool.submit(tasks.stop_workers)
            try:
                assert tasks._STOP.wait(timeout=2)
                assert not stopping.done()
                assert beat.wait(timeout=2)
            finally:
                release.set()
            stopping.result(timeout=2)
        assert tasks.get(task_id)["status"] == "done"
        assert tasks._WORKERS == []
        assert tasks._HEARTBEAT_THREAD is None
    finally:
        release.set()
        tasks.stop_workers()


def test_worker_survives_transient_claim_database_error(monkeypatch):
    retried = threading.Event()
    calls = []

    def run_one(**kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise sqlite3.OperationalError("synthetic busy database")
        retried.set()
        return False

    monkeypatch.setattr(tasks, "run_one", run_one)
    tasks.start_workers(1)
    try:
        assert retried.wait(timeout=2)
    finally:
        tasks.stop_workers()


def test_lifespan_initializes_client_before_workers_and_closes_it_last(monkeypatch):
    import asyncio

    from app.runtime_lock import exclusive_data_dir

    events = []
    client = object()
    manager = SimpleNamespace(
        client=client,
        base_url="https://provider.example.test/v1",
        start=lambda: events.append("client-start"),
        stop=lambda: events.append("client-stop"),
    )
    monkeypatch.setattr(config, "APP_SKIP_LLM", False)
    monkeypatch.setattr(main.llm, "ServerManager", lambda: manager)
    monkeypatch.setattr(runtime, "manager", None)
    monkeypatch.setenv("TASK_WORKERS", "3")
    for module, name in [
        (store, "init_db"),
        (main.auth, "init_auth"),
        (profile, "ensure_dirs"),
        (main.interview_state, "ensure_state"),
        (main.docs, "ensure_templates"),
        (main.sources, "ensure_sources_file"),
        (store, "recover_interrupted_work"),
    ]:
        monkeypatch.setattr(module, name, lambda: None)
    monkeypatch.setattr(store, "load_token_df", lambda: ({}, 0))
    monkeypatch.setattr(main.sources, "load_sources", lambda: [])
    monkeypatch.setattr(store, "sync_sources", lambda sources: None)
    monkeypatch.setattr(tasks, "recover_interrupted", lambda: events.append("recover"))
    monkeypatch.setattr(main.scheduler, "start", lambda: events.append("scheduler-start"))
    monkeypatch.setattr(main.scheduler, "stop", lambda: events.append("scheduler-stop"))
    monkeypatch.setattr(tasks, "stop_workers", lambda: events.append("workers-stop"))

    def start_workers(count, *, resolve_owner):
        assert count == 3
        assert runtime.manager.client is client
        assert resolve_owner is main._resolve_task_owner
        events.append("workers-start")

    monkeypatch.setattr(tasks, "start_workers", start_workers)

    async def serve():
        async with main.lifespan(main.app):
            with pytest.raises(RuntimeError, match="already running"):
                with exclusive_data_dir(config.DATA_DIR):
                    pytest.fail("lock released before shutdown")
            raise ValueError("synthetic lifespan body failure")

    with pytest.raises(ValueError, match="synthetic lifespan body failure"):
        asyncio.run(serve())
    assert events == [
        "recover",
        "client-start",
        "workers-start",
        "scheduler-start",
        "scheduler-stop",
        "workers-stop",
        "client-stop",
    ]
    with exclusive_data_dir(config.DATA_DIR):
        pass


def test_running_task_cannot_be_cancelled_to_bypass_owner_concurrency():
    running = tasks.enqueue("document", owner="a")
    tasks.claim_next()
    waiting = tasks.enqueue("match", owner="a")
    assert tasks.cancel(running, owner="a") is False
    assert tasks.claim_next() is None
    assert tasks.cancel(waiting, owner="a") is True
    assert tasks.get(waiting, owner="a")["status"] == "cancelled"
    tasks.finish(running)
    assert tasks.get(running, owner="a")["status"] == "done"


def test_terminal_states_cannot_be_overwritten_by_late_callbacks():
    task_id = tasks.enqueue("scan", owner=None)
    assert tasks.cancel(task_id, owner=None)
    tasks.finish(task_id)
    tasks.fail(task_id, "late error")
    tasks.heartbeat(task_id)
    assert tasks.get(task_id)["status"] == "cancelled"
    assert tasks.get(task_id)["heartbeat_at"] is None
