"""The web app queues; only the worker runs background work."""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app import config, runtime, runtime_lock, scheduler, store, tasks, worker
from tests.test_jobs_page import client, soup  # noqa: F401


@pytest.mark.parametrize(
    "held,wanted,conflict",
    [
        ("web", "worker", False),
        ("worker", "web", False),
        ("web", "web", True),
        ("worker", "worker", True),
        ("all", "web", True),
        ("all", "worker", True),
        ("web", "all", True),
        ("worker", "all", True),
    ],
)
def test_each_role_runs_once_and_combined_overlaps_neither(tmp_path, held, wanted, conflict):
    with runtime_lock.exclusive_data_dir(tmp_path, held):
        if conflict:
            with (
                pytest.raises(RuntimeError, match="already running"),
                runtime_lock.exclusive_data_dir(tmp_path, wanted),
            ):
                pass
        else:
            with runtime_lock.exclusive_data_dir(tmp_path, wanted):
                pass


def test_failed_combined_lock_releases_what_it_took(tmp_path):
    with (
        runtime_lock.exclusive_data_dir(tmp_path, "worker"),
        pytest.raises(RuntimeError),
        runtime_lock.exclusive_data_dir(tmp_path, "all"),
    ):
        pass
    with runtime_lock.exclusive_data_dir(tmp_path, "web"):
        pass


def test_unknown_role_is_refused(monkeypatch):
    monkeypatch.setenv("APP_ROLE", "everything")
    with pytest.raises(RuntimeError, match="APP_ROLE"):
        runtime.app_role()


def _record(monkeypatch):
    started = []
    monkeypatch.setattr(tasks, "start_workers", lambda *a, **k: started.append("workers"))
    monkeypatch.setattr(tasks, "stop_workers", lambda: started.append("stop-workers"))
    monkeypatch.setattr(scheduler, "start", lambda: started.append("scheduler"))
    monkeypatch.setattr(scheduler, "stop", lambda: started.append("stop-scheduler"))
    monkeypatch.setattr(tasks, "recover_interrupted", lambda: started.append("recover") or 0)
    monkeypatch.setattr(
        store, "recover_interrupted_work", lambda: started.append("recover-state") or 0
    )
    return started


def test_web_role_never_touches_background_work(monkeypatch):
    started = _record(monkeypatch)
    with runtime.services("web"):
        assert started == []
    assert started == []


def test_worker_role_owns_background_work(monkeypatch):
    started = _record(monkeypatch)
    with runtime.services("worker"):
        assert started == ["recover-state", "recover", "workers", "scheduler"]
    assert started[-2:] == ["stop-scheduler", "stop-workers"]


def test_task_queued_by_web_stays_queued_until_a_worker_claims_it(monkeypatch):
    monkeypatch.setattr(tasks, "start_workers", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "stop_workers", lambda: None)
    with runtime.services("web"):
        task_id = tasks.enqueue("scan", owner=None, dedupe_key="role-test")
    assert tasks.get(task_id)["status"] == "queued"
    assert tasks.claim_next()["id"] == task_id
    tasks.finish(task_id, {})


def test_restarting_web_leaves_a_running_task_alone(monkeypatch):
    task_id = tasks.enqueue("scan", owner=None, dedupe_key="role-test-2")
    assert tasks.claim_next()["id"] == task_id
    with runtime.services("web"):
        pass
    assert tasks.get(task_id)["status"] == "running"
    tasks.finish(task_id, {})


def test_worker_heartbeats_until_stopped(monkeypatch):
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(tasks, "start_workers", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "stop_workers", lambda: None)
    monkeypatch.setattr(scheduler, "start", lambda: None)
    monkeypatch.setattr(scheduler, "stop", lambda: None)
    thread = threading.Thread(target=worker.run)
    thread.start()
    for _ in range(200):
        if store.meta_get("worker_heartbeat"):
            break
        threading.Event().wait(0.01)
    worker._STOP.set()
    thread.join(5)
    assert not thread.is_alive()
    assert store.meta_get("worker_heartbeat")


def _admin_state(monkeypatch, seen):
    from app.routers import pages

    monkeypatch.setenv("APP_ROLE", "web")
    if seen:
        store.meta_set("worker_heartbeat", seen.isoformat())
    return pages._worker_state()


def test_admin_knows_a_live_and_a_silent_worker(client, monkeypatch):  # noqa: F811
    assert _admin_state(monkeypatch, datetime.now(UTC))["alive"] is True
    late = datetime.now(UTC) - timedelta(minutes=10)
    assert _admin_state(monkeypatch, late)["alive"] is False


def test_combined_role_shows_no_worker_warning(monkeypatch):
    from app.routers import pages

    monkeypatch.delenv("APP_ROLE", raising=False)
    assert pages._worker_state() == {"separate": False}


def test_compose_splits_roles_within_the_shared_host_limits():
    import yaml

    data = yaml.safe_load((config.BASE_DIR / "compose.yaml").read_text())["services"]
    assert data["app"]["environment"]["APP_ROLE"] == "web"
    assert data["worker"]["environment"]["APP_ROLE"] == "worker"
    assert data["worker"]["volumes"] == data["app"]["volumes"]
    assert data["app"]["cpus"] + data["worker"]["cpus"] <= 2.0
    assert "ports" not in data["worker"]


def test_interrupted_scan_is_not_counted_against_sources(monkeypatch):
    from app import source_http, sources

    monkeypatch.setattr(sources, "load_sources", lambda: [])
    source_http.STOPPING.set()
    try:
        with pytest.raises(RuntimeError, match="onderbroken"):
            sources.scan()
    finally:
        source_http.STOPPING.clear()
    assert not store.meta_get("source_health")
