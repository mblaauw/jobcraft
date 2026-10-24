"""Real scan dispatch and retry, with fake adapters and isolated storage."""

import json
from types import SimpleNamespace

from app import main, store, tasks, tenancy


def test_real_scan_handler_persists_result_and_enqueues_profile_owners(monkeypatch):
    summary = {"new": 3, "updated": 2, "errors": []}
    calls = []
    monkeypatch.setattr(main.sources, "scan", lambda: calls.append(tenancy.current()) or summary)
    monkeypatch.setattr(main.feedback, "refresh_document_frequency", lambda: None)
    monkeypatch.setattr(store, "active_owners_with_profile", lambda: ["a", "b"])
    monkeypatch.setenv("MATCH_AFTER_SCAN", "1")
    task_id = tasks.enqueue("scan", owner=None, dedupe_key="scan")
    assert tasks.run_one()
    task = tasks.get(task_id)
    assert task["status"] == "done", task["error"]
    assert json.loads(task["result_json"]) == summary
    assert calls == [None]
    assert tasks.latest("match", owner="a")["status"] == "queued"
    assert tasks.latest("match", owner="b")["status"] == "queued"


def test_scan_failure_is_durable_and_manual_retry_ignores_stale_meta(monkeypatch):
    def broken_scan():
        raise RuntimeError("synthetic scan failure")

    monkeypatch.setattr(main.sources, "scan", broken_scan)
    task_id = tasks.enqueue("scan", owner=None, dedupe_key="scan")
    assert tasks.run_one()
    task = tasks.get(task_id)
    assert task["status"] == "error"
    assert "synthetic scan failure" in task["error"]
    assert tasks.latest("match", owner="a") is None

    monkeypatch.setattr(main.auth, "require_admin", lambda request: None)
    store.meta_set("scan_status", "running")  # left by the old broken dispatcher
    response = main.sources_scan(SimpleNamespace())
    assert response.status_code == 303
    retry = tasks.latest("scan")
    assert retry["id"] != task_id and retry["status"] == "queued"
    main.sources_scan(SimpleNamespace())
    assert tasks.latest("scan")["id"] == retry["id"]


def test_scheduled_scan_sets_running_status_and_honors_no_fanout(monkeypatch):
    seen = []
    monkeypatch.setattr(
        main.sources, "scan", lambda: seen.append(store.meta_get("scan_status")) or {"new": 0}
    )
    monkeypatch.setattr(main.feedback, "refresh_document_frequency", lambda: None)
    monkeypatch.setattr(store, "active_owners_with_profile", lambda: ["a"])
    monkeypatch.setenv("MATCH_AFTER_SCAN", "0")
    tasks.enqueue("scan", owner=None, dedupe_key="scan")
    assert tasks.run_one()
    assert seen == ["running"]
    assert store.meta_get("scan_status") == "done"
    assert tasks.latest("match", owner="a") is None
