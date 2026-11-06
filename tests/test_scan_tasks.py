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


def test_sources_are_scanned_in_catalogue_order_not_alphabetically():
    for source_id in ("zuid", "academisch", "midden"):
        store.save_source_config({"id": source_id, "kind": "rss"})
    store.save_source_config({"id": "zuid", "kind": "rss", "revision": 2})  # an update keeps its place
    order = [entry["id"] for entry in store.load_source_configs()]
    assert [source_id for source_id in order if source_id in {"zuid", "academisch", "midden"}] == ["zuid", "academisch", "midden"]


def test_new_vacancies_are_ranked_during_a_scan_not_only_after_it(monkeypatch):
    from app import sources

    def adapter(new):
        def scan(client, source, entry, summary, limit):
            entry["new"] += new
        return scan

    clock = iter([0.0, 10.0, 500.0, 510.0])
    monkeypatch.setattr(sources.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sources, "registry", lambda: {"one": adapter(3), "none": adapter(0)})
    monkeypatch.setattr(sources, "load_sources", lambda: [
        {"id": "a", "kind": "one"},  # first new ones: rank at once (clock 0)
        {"id": "b", "kind": "one"},  # 10 s later: too soon
        {"id": "c", "kind": "none"},  # nothing new: no reason
        {"id": "d", "kind": "one"},  # minutes later: rank again (clock 500)
    ])
    monkeypatch.setattr(store, "active_owners_with_profile", lambda: ["a", "b"])
    enqueued = []
    monkeypatch.setattr(sources.tasks, "enqueue", lambda kind, owner, dedupe_key: enqueued.append((kind, owner)))
    sources.scan()
    assert enqueued == [("rerank", "a"), ("rerank", "b")] * 2


def test_a_restart_during_a_scan_marks_no_source_as_failed(monkeypatch):
    import pytest

    from app import sources
    from app.source_http import STOPPING

    def stopping(client, source, entry, summary, limit):
        STOPPING.set()
        raise RuntimeError("De server stopt; de scan is onderbroken.")

    marked = []
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sources, "registry", lambda: {"stop": stopping})
    monkeypatch.setattr(sources, "load_sources", lambda: [{"id": "a", "kind": "stop"}, {"id": "b", "kind": "stop"}])
    monkeypatch.setattr(store, "update_source_status", lambda source_id, status: marked.append((source_id, status)))
    try:
        with pytest.raises(RuntimeError, match="herstart"):
            sources.scan()
    finally:
        STOPPING.clear()
    assert marked == []


def test_a_failing_source_keeps_its_reason_and_the_scan_goes_on(monkeypatch):
    import json

    from app import sources

    def broken(client, source, entry, summary, limit):
        raise RuntimeError("Niet bereikbaar: board-x")

    def fine(client, source, entry, summary, limit):
        entry["items"] = 3

    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sources, "registry", lambda: {"broken": broken, "fine": fine})
    monkeypatch.setattr(sources, "load_sources", lambda: [{"id": "a", "kind": "broken"}, {"id": "b", "kind": "fine"}, {"id": "c", "kind": "nog-niet"}])
    summary = sources.scan()
    assert summary["sources"]["a"] == {"status": "error", "new": 0, "updated": 0, "items": 0, "reason": "RuntimeError: Niet bereikbaar: board-x"}
    assert summary["sources"]["b"]["status"] == "ok" and summary["sources"]["b"]["items"] == 3
    assert summary["sources"]["c"]["status"] == "skipped"
    health = json.loads(store.meta_get("source_health"))
    assert health["a"]["reason"] == "RuntimeError: Niet bereikbaar: board-x" and health["a"]["failures"] == 1
    monkeypatch.setattr(sources, "registry", lambda: {"broken": fine, "fine": fine})
    sources.scan()
    assert json.loads(store.meta_get("source_health"))["a"]["reason"] == ""  # a good round clears it
