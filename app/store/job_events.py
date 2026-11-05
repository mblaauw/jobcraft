"""SQLite job_events operations: what happened to each vacancy, per account, in order."""

from __future__ import annotations

import json

from .. import tenancy
from .connection import connect, now

KINDS = ("status", "judgement", "reason", "route", "deeper", "verdict")


def _add(con, job_id: int, kind: str, value: str, previous: str | None = None, detail: dict | None = None) -> None:
    if kind not in KINDS:
        raise ValueError("Unknown job event kind")
    # A vacancy that is gone has no history to keep; the row would break the foreign key.
    con.execute(
        "INSERT INTO job_events(owner, job_id, kind, value, previous, at, detail) "
        "SELECT ?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM jobs WHERE id=?)",
        (
            tenancy.user_id(),
            job_id,
            kind,
            value,
            previous,
            now(),
            json.dumps(detail, separators=(",", ":")) if detail else None,
            job_id,
        ),
    )


def record_job_event(job_id: int, kind: str, value: str, detail: dict | None = None) -> None:
    with connect() as con:
        _add(con, job_id, kind, value, detail=detail)


def _last_values(con, kind: str) -> dict[int, str]:
    rows = con.execute(
        "SELECT job_id, value FROM job_events WHERE id IN ("
        "SELECT MAX(id) FROM job_events WHERE owner=? AND kind=? GROUP BY job_id)",
        (tenancy.user_id(), kind),
    ).fetchall()
    return {row["job_id"]: row["value"] for row in rows}


def record_routes(routes: dict[int, str]) -> None:
    """Store each vacancy's route only when it differs from the last one stored."""
    with connect() as con:
        last = _last_values(con, "route")
        changed = {job_id: value for job_id, value in routes.items() if last.get(job_id) != value}
        for job_id, value in changed.items():
            _add(con, job_id, "route", value, previous=last.get(job_id))


def list_job_events(job_id: int | None = None, kind: str | None = None) -> list[dict]:
    """This account's events, oldest first, optionally for one vacancy or one kind."""
    clauses, params = ["owner=?"], [tenancy.user_id()]
    if job_id is not None:
        clauses.append("job_id=?")
        params.append(job_id)
    if kind is not None:
        clauses.append("kind=?")
        params.append(kind)
    with connect() as con:
        rows = con.execute(
            "SELECT job_id, kind, value, previous, at, detail FROM job_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def status_funnel() -> dict[str, int]:
    """How many vacancies ever reached each status, however briefly: the real funnel."""
    with connect() as con:
        rows = con.execute(
            "SELECT value, COUNT(DISTINCT job_id) AS n FROM job_events WHERE owner=? AND kind='status' GROUP BY value",
            (tenancy.user_id(),),
        ).fetchall()
    return {row["value"]: row["n"] for row in rows}
