"""SQLite-backed task queue primitives."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from . import store, tenancy

LLM_KINDS = ("match", "consolidate", "document")
HEARTBEAT_INTERVAL = 30.0
log = logging.getLogger("jobcraft.tasks")


def enqueue(
    kind: str,
    *,
    owner: str | None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> int:
    with store.connect() as con:
        row = con.execute(
            "INSERT INTO tasks(owner, kind, payload_json, dedupe_key, created_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(dedupe_key) WHERE status IN ('queued','running') "
            "DO UPDATE SET dedupe_key=excluded.dedupe_key RETURNING id, owner",
            (owner, kind, json.dumps(payload or {}, ensure_ascii=False), dedupe_key, store.now()),
        ).fetchone()
        if row["owner"] != owner:
            raise ValueError("dedupe key belongs to another owner")
        return int(row["id"])


def claim_next() -> dict | None:
    with store.connect() as con:
        row = con.execute(
            "UPDATE tasks SET status='running', started_at=?, heartbeat_at=?, "
            "attempts=attempts+1 WHERE id=("
            "SELECT id FROM tasks t WHERE status='queued' "
            "AND (kind != 'scan' OR NOT EXISTS ("
            "  SELECT 1 FROM tasks running WHERE running.kind='scan' AND running.status='running')) "
            "AND (kind NOT IN ('match','consolidate','document') OR owner IS NULL OR NOT EXISTS ("
            "  SELECT 1 FROM tasks running WHERE running.owner=t.owner "
            "  AND running.kind IN ('match','consolidate','document') AND running.status='running')) "
            "ORDER BY id LIMIT 1) AND status='queued' RETURNING *",
            (store.now(), store.now()),
        ).fetchone()
        return dict(row) if row else None


def heartbeat(task_id: int) -> None:
    with store.connect() as con:
        con.execute(
            "UPDATE tasks SET heartbeat_at=? WHERE id=? AND status='running'",
            (store.now(), task_id),
        )


def finish(task_id: int, result: dict | None = None) -> None:
    with store.connect() as con:
        con.execute(
            "UPDATE tasks SET status='done', result_json=?, finished_at=? "
            "WHERE id=? AND status='running'",
            (json.dumps(result or {}, ensure_ascii=False), store.now(), task_id),
        )


def fail(task_id: int, error: str) -> None:
    with store.connect() as con:
        con.execute(
            "UPDATE tasks SET status='error', error=?, finished_at=? "
            "WHERE id=? AND status='running'",
            (error[:1000], store.now(), task_id),
        )


def cancel(task_id: int, *, owner: str | None) -> bool:
    """Cancel queued work only; running Python handlers cannot be preempted safely."""
    with store.connect() as con:
        cursor = con.execute(
            "UPDATE tasks SET status='cancelled', finished_at=? "
            "WHERE id=? AND owner IS ? AND status='queued'",
            (store.now(), task_id, owner),
        )
    return cursor.rowcount > 0


def list_for_owner(owner: str, limit: int = 20) -> list[dict]:
    with store.connect() as con:
        rows = con.execute(
            "SELECT * FROM tasks WHERE owner=? ORDER BY id DESC LIMIT ?", (owner, limit)
        ).fetchall()
    return [dict(row) for row in rows]


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
                "SELECT * FROM tasks WHERE kind=? AND dedupe_key=? AND owner IS ? "
                "ORDER BY id DESC LIMIT 1",
                (kind, key, owner),
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
_HEARTBEAT_STOP = threading.Event()
_HEARTBEAT_THREAD: threading.Thread | None = None
_RUNNING: set[int] = set()
_RUNNING_GUARD = threading.Lock()


def handler(kind: str):
    def register(function: Callable[[dict], dict | None]):
        _HANDLERS[kind] = function
        return function

    return register


def run_one(*, resolve_owner: Callable[[str], dict | None] | None = None) -> bool:
    task = claim_next()
    if task is None:
        return False
    function = _HANDLERS.get(task["kind"])
    if function is None:
        fail(task["id"], f"geen handler voor {task['kind']}")
        return True
    with _RUNNING_GUARD:
        _RUNNING.add(task["id"])
    try:
        principal = None
        if task["owner"] is not None:
            principal = resolve_owner(task["owner"]) if resolve_owner else None
            if principal is None or principal.get("id") != task["owner"]:
                raise RuntimeError("Taakeigenaar bestaat niet of is niet actief.")
        with tenancy.scope(principal):
            result = function(task)
        finish(task["id"], result if isinstance(result, dict) else {})
    except Exception as exc:  # noqa: BLE001 - the queue must keep running
        fail(task["id"], f"{type(exc).__name__}: {exc}")
    finally:
        with _RUNNING_GUARD:
            _RUNNING.discard(task["id"])
    return True


def start_workers(
    count: int = 2, *, resolve_owner: Callable[[str], dict | None] | None = None
) -> None:
    global _HEARTBEAT_THREAD
    if _WORKERS or _HEARTBEAT_THREAD is not None:
        raise RuntimeError("task workers are already running")
    _STOP.clear()
    _HEARTBEAT_STOP.clear()

    def loop():
        while not _STOP.is_set():
            try:
                worked = run_one(resolve_owner=resolve_owner)
            except sqlite3.OperationalError:
                log.warning("Task queue temporarily unavailable; retrying.")
                worked = False
            if not worked:
                _STOP.wait(0.5)

    def keep_alive():
        while not _HEARTBEAT_STOP.wait(HEARTBEAT_INTERVAL):
            with _RUNNING_GUARD:
                running = tuple(_RUNNING)
            for task_id in running:
                try:
                    heartbeat(task_id)
                except sqlite3.OperationalError:
                    log.warning("Task heartbeat temporarily unavailable.")

    _HEARTBEAT_THREAD = threading.Thread(target=keep_alive, daemon=True)
    _HEARTBEAT_THREAD.start()
    for _ in range(max(1, count)):
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        _WORKERS.append(thread)


def stop_workers() -> None:
    """Finish current handlers before clients and the process lock are released."""
    global _HEARTBEAT_THREAD
    _STOP.set()
    for thread in _WORKERS:
        thread.join()
    _WORKERS.clear()
    _HEARTBEAT_STOP.set()
    if _HEARTBEAT_THREAD is not None:
        _HEARTBEAT_THREAD.join()
        _HEARTBEAT_THREAD = None


def recover_interrupted() -> int:
    """Only at startup while holding the exclusive data-directory process lock."""
    with store.connect() as con:
        cursor = con.execute(
            "UPDATE tasks SET status='error', error=?, finished_at=? WHERE status='running'",
            ("Onderbroken door een herstart. Probeer opnieuw.", store.now()),
        )
    return cursor.rowcount


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
