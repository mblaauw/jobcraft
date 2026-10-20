"""SQLite-backed task queue primitives."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from . import store

LLM_KINDS = ("match", "consolidate", "document")


def enqueue(
    kind: str,
    *,
    owner: str | None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> int:
    with store.connect() as con:
        if dedupe_key:
            existing = con.execute(
                "SELECT id FROM tasks WHERE dedupe_key=? AND status IN ('queued','running')",
                (dedupe_key,),
            ).fetchone()
            if existing:
                return int(existing["id"])
        cursor = con.execute(
            "INSERT INTO tasks(owner, kind, payload_json, dedupe_key, created_at) "
            "VALUES (?,?,?,?,?)",
            (owner, kind, json.dumps(payload or {}, ensure_ascii=False), dedupe_key, store.now()),
        )
        return int(cursor.lastrowid)


def claim_next() -> dict | None:
    with store.connect() as con:
        row = con.execute(
            "SELECT * FROM tasks t WHERE status='queued' "
            "AND (kind != 'scan' OR NOT EXISTS ("
            "  SELECT 1 FROM tasks running WHERE running.kind='scan' AND running.status='running')) "
            "AND (kind NOT IN ('match','consolidate','document') OR owner IS NULL OR NOT EXISTS ("
            "  SELECT 1 FROM tasks running WHERE running.owner=t.owner "
            "  AND running.kind IN ('match','consolidate','document') AND running.status='running')) "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        con.execute(
            "UPDATE tasks SET status='running', started_at=?, heartbeat_at=?, "
            "attempts=attempts+1 WHERE id=?",
            (store.now(), store.now(), row["id"]),
        )
        return dict(row) | {"status": "running"}


def heartbeat(task_id: int) -> None:
    with store.connect() as con:
        con.execute("UPDATE tasks SET heartbeat_at=? WHERE id=?", (store.now(), task_id))


def finish(task_id: int, result: dict | None = None) -> None:
    with store.connect() as con:
        con.execute(
            "UPDATE tasks SET status='done', result_json=?, finished_at=? WHERE id=?",
            (json.dumps(result or {}, ensure_ascii=False), store.now(), task_id),
        )


def fail(task_id: int, error: str) -> None:
    with store.connect() as con:
        con.execute(
            "UPDATE tasks SET status='error', error=?, finished_at=? WHERE id=?",
            (error[:1000], store.now(), task_id),
        )


def cancel(task_id: int, *, owner: str | None) -> bool:
    with store.connect() as con:
        cursor = con.execute(
            "UPDATE tasks SET status='cancelled', finished_at=? "
            "WHERE id=? AND owner IS ? AND status IN ('queued','running')",
            (store.now(), task_id, owner),
        )
    return cursor.rowcount > 0


def get(task_id: int, *, owner: str | None = None) -> dict | None:
    with store.connect() as con:
        row = con.execute(
            "SELECT * FROM tasks WHERE id=? AND owner IS ?", (task_id, owner)
        ).fetchone()
    return dict(row) if row else None


def latest(kind: str, *, owner: str | None = None, key: str | None = None) -> dict | None:
    with store.connect() as con:
        if key is not None:
            row = con.execute(
                "SELECT * FROM tasks WHERE kind=? AND dedupe_key=? ORDER BY id DESC LIMIT 1",
                (kind, key),
            ).fetchone()
        elif owner is None:
            row = con.execute(
                "SELECT * FROM tasks WHERE kind=? AND owner IS NULL ORDER BY id DESC LIMIT 1",
                (kind,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM tasks WHERE kind=? AND owner=? ORDER BY id DESC LIMIT 1",
                (kind, owner),
            ).fetchone()
    return dict(row) if row else None


_HANDLERS: dict[str, Callable[[dict], dict | None]] = {}
_STOP = threading.Event()
_WORKERS: list[threading.Thread] = []


def handler(kind: str):
    def register(function: Callable[[dict], dict | None]):
        _HANDLERS[kind] = function
        return function

    return register


def run_one() -> bool:
    task = claim_next()
    if task is None:
        return False
    function = _HANDLERS.get(task["kind"])
    if function is None:
        fail(task["id"], f"geen handler voor {task['kind']}")
        return True
    try:
        result = function(task)
        finish(task["id"], result if isinstance(result, dict) else {})
    except Exception as exc:  # noqa: BLE001 - the queue must keep running
        fail(task["id"], f"{type(exc).__name__}: {exc}")
    return True


def start_workers(count: int = 2) -> None:
    _STOP.clear()

    def loop():
        while not _STOP.is_set():
            if not run_one():
                _STOP.wait(0.5)

    for _ in range(max(1, count)):
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        _WORKERS.append(thread)


def stop_workers() -> None:
    _STOP.set()
    for thread in _WORKERS:
        thread.join(timeout=5)
    _WORKERS.clear()


def recover_stale(timeout_seconds: int = 120) -> int:
    cutoff = (datetime.now(UTC) - timedelta(seconds=timeout_seconds)).isoformat()
    with store.connect() as con:
        rows = con.execute(
            "SELECT id FROM tasks WHERE status='running' AND heartbeat_at < ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            con.execute(
                "UPDATE tasks SET status='error', error=?, finished_at=? WHERE id=?",
                ("Onderbroken door een herstart. Probeer opnieuw.", store.now(), row["id"]),
            )
    return len(rows)
