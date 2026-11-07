"""SQLite-backed task queue primitives."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from . import store, tenancy

LLM_KINDS = ("match", "consolidate", "document")
# Whole rounds that are safe to start over, so a restart puts them back in the queue once.
RESUMABLE_KINDS = ("scan", "match")
INTERRUPTED = "Onderbroken door een herstart."
HEARTBEAT_INTERVAL = 30.0
log = logging.getLogger("jobcraft.tasks")


class Interrupted(RuntimeError):
    """Raised by a handler that was cut off by a restart: the work did not fail, it did not finish."""


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


def failures_overview(days: int = 7) -> list[dict]:
    """Failed work per kind. No error text or owner: admins may not see people's data."""
    start = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with store.connect() as con:
        rows = con.execute(
            "SELECT kind, COUNT(*) AS n, MAX(finished_at) AS last FROM tasks "
            "WHERE status='error' AND finished_at >= ? GROUP BY kind ORDER BY last DESC",
            (start,),
        ).fetchall()
    return [dict(row) for row in rows]


def open_count() -> int:
    """Work waiting or running right now."""
    with store.connect() as con:
        row = con.execute("SELECT COUNT(*) AS n FROM tasks WHERE status IN ('queued','running')").fetchone()
    return int(row["n"] or 0)


_EXCEPTION_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*(Error|Exception|Timeout|Warning)\b")


def failure_kinds(days: int = 7) -> list[dict]:
    """Failed work per kind that has not succeeded since for the same account, with the
    exception's name only: never its message, which may quote a person.

    `interrupted` says every one of them was cut off by a restart rather than failing."""
    start = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with store.connect() as con:
        rows = con.execute(
            "SELECT kind, error, finished_at FROM tasks t WHERE status='error' AND finished_at >= ? "
            "AND NOT EXISTS (SELECT 1 FROM tasks d WHERE d.kind=t.kind AND d.owner IS t.owner "
            "AND d.status='done' AND d.id > t.id) ORDER BY id",
            (start,),
        ).fetchall()
    kinds: dict[str, dict] = {}
    for row in rows:
        error = (row["error"] or "").strip()
        item = kinds.setdefault(row["kind"], {"kind": row["kind"], "n": 0, "interrupted": True})
        match = _EXCEPTION_NAME.match(error)
        item.update(n=item["n"] + 1, last=row["finished_at"], error=match.group(0) if match else "")
        item["interrupted"] = item["interrupted"] and "herstart" in error.lower()
    return sorted(kinds.values(), key=lambda item: item["last"] or "", reverse=True)


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


def handler(kind: str) -> Callable[[Callable[[dict], dict | None]], Callable[[dict], dict | None]]:
    def register(function: Callable[[dict], dict | None]) -> Callable[[dict], dict | None]:
        _HANDLERS[kind] = function
        return function

    return register


def run_one(*, resolve_owner: Callable[[str], dict | None] | None = None) -> bool:
    if _STOP.is_set() and _WORKERS:  # a round requeued at shutdown waits for the next start
        return False
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
    except Interrupted:
        interrupted(task["id"])
    except Exception as exc:
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

    def loop() -> None:
        while not _STOP.is_set():
            try:
                worked = run_one(resolve_owner=resolve_owner)
            except sqlite3.OperationalError:
                log.warning("Task queue temporarily unavailable; retrying.")
                worked = False
            if not worked:
                _STOP.wait(0.5)

    def keep_alive() -> None:
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


def _close_interrupted(con: sqlite3.Connection, row: sqlite3.Row, *, resume: bool = True) -> bool:
    """End a running task that a restart cut off.

    A scan or match round goes back in the queue once, with its attempts carried over,
    so the second interruption stays an error and a round that keeps dying does not
    loop. The old row says what happened; only a real failure keeps status 'error'.
    A stale heartbeat is not a restart: its handler may still be running, so nothing
    is queued beside it.
    """
    again = resume and row["kind"] in RESUMABLE_KINDS and int(row["attempts"] or 0) < 2
    con.execute(
        "UPDATE tasks SET status=?, error=?, finished_at=? WHERE id=? AND status='running'",
        (
            "interrupted" if again else "error",
            INTERRUPTED + (" Staat opnieuw in de wachtrij." if again else " Probeer opnieuw."),
            store.now(),
            row["id"],
        ),
    )
    if again:
        con.execute(
            "INSERT INTO tasks(owner, kind, payload_json, dedupe_key, attempts, created_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(dedupe_key) WHERE status IN ('queued','running') DO NOTHING",
            (row["owner"], row["kind"], row["payload_json"], row["dedupe_key"], row["attempts"], store.now()),
        )
    return again


def interrupted(task_id: int) -> bool:
    """A handler reported that a restart cut it off; True when the round is queued again."""
    with store.connect() as con:
        row = con.execute("SELECT * FROM tasks WHERE id=? AND status='running'", (task_id,)).fetchone()
        return bool(row) and _close_interrupted(con, row)


def recover_interrupted() -> int:
    """Only at startup while holding the exclusive data-directory process lock."""
    with store.connect() as con:
        rows = con.execute("SELECT * FROM tasks WHERE status='running'").fetchall()
        for row in rows:
            _close_interrupted(con, row)
    return len(rows)


def recover_stale(timeout_seconds: int = 120) -> int:
    cutoff = (datetime.now(UTC) - timedelta(seconds=timeout_seconds)).isoformat()
    with store.connect() as con:
        rows = con.execute(
            "SELECT * FROM tasks WHERE status='running' AND heartbeat_at < ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            _close_interrupted(con, row, resume=False)
    return len(rows)
