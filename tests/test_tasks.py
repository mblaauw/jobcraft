from __future__ import annotations

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

    task_id = tasks.enqueue("fixture", owner="a")
    assert tasks.run_one() is True
    assert seen == [task_id]
    assert tasks.latest("fixture", owner="a")["status"] == "done"

    @tasks.handler("broken")
    def broken(task):
        raise RuntimeError("boom")

    failed = tasks.enqueue("broken", owner="a")
    assert tasks.run_one() is True
    assert tasks.latest("broken", owner="a")["status"] == "error"
    assert "boom" in tasks.latest("broken", owner="a")["error"]
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
