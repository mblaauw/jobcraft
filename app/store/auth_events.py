"""SQLite auth_events operations: sign-ins, sign-outs and what administrators did to accounts."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from .connection import connect, now

KINDS = (
    "login_ok",
    "login_failed",
    "logout",
    "invite_used",
    "password_changed",
    "password_reset",
    "deactivated",
    "activated",
)
AGENT_LIMIT = 200
DEFAULT_RETENTION_DAYS = 90


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get("AUTH_LOG_DAYS", DEFAULT_RETENTION_DAYS)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def record_auth_event(
    kind: str,
    *,
    user_id: str | None = None,
    username: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    actor_id: str | None = None,
) -> None:
    """One row per moment. The typed name is kept for failed attempts only: a typo must be
    visible without pretending it names a real account."""
    if kind not in KINDS:
        raise ValueError("Unknown auth event kind")
    typed = (username or "").strip()[:64] if kind == "login_failed" else ""
    with connect() as con:
        con.execute(
            "INSERT INTO auth_events(at, kind, user_id, username, ip, user_agent, actor_id) VALUES (?,?,?,?,?,?,?)",
            (
                now(),
                kind,
                user_id,
                typed or None,
                (ip or "")[:64] or None,
                (user_agent or "")[:AGENT_LIMIT] or None,
                actor_id,
            ),
        )


def _since(days: int | None) -> str | None:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat() if days else None


PAGE_LIMIT = 500


def list_auth_events(
    *, user_id: str | None = None, kind: str | None = None, days: int | None = None, limit: int = PAGE_LIMIT
) -> list[dict]:
    """Newest first, optionally for one account, one kind or the last so many days.

    At most ``limit`` rows: a page that gets exactly that many knows it is cut off.
    """
    clauses, params = [], []
    if user_id:
        clauses.append("user_id=?")
        params.append(user_id)
    if kind:
        clauses.append("kind=?")
        params.append(kind)
    if since := _since(days):
        clauses.append("at>=?")
        params.append(since)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        rows = con.execute(
            "SELECT id, at, kind, user_id, username, ip, user_agent, actor_id FROM auth_events"
            + where
            + " ORDER BY id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def last_logins() -> dict[str, dict]:
    """The latest successful sign-in per account: when, from where, with what."""
    with connect() as con:
        rows = con.execute(
            "SELECT user_id, at, ip, user_agent FROM auth_events WHERE id IN ("
            "SELECT MAX(id) FROM auth_events WHERE kind IN ('login_ok','invite_used') AND user_id IS NOT NULL GROUP BY user_id)"
        ).fetchall()
    return {row["user_id"]: dict(row) for row in rows}


def failed_attempts(days: int = 1) -> list[dict]:
    """Failed sign-ins in the period, grouped by address: the view that shows a guesser."""
    with connect() as con:
        rows = con.execute(
            "SELECT ip, COUNT(*) AS n, COUNT(DISTINCT COALESCE(user_id, username)) AS names, MAX(at) AS last "
            "FROM auth_events WHERE kind='login_failed' AND at>=? GROUP BY ip ORDER BY n DESC, last DESC",
            (_since(days),),
        ).fetchall()
    return [dict(row) for row in rows]


def failed_per_user(days: int = 1) -> dict[str, int]:
    """Failed sign-ins on existing accounts in the period, per account."""
    with connect() as con:
        rows = con.execute(
            "SELECT user_id, COUNT(*) AS n FROM auth_events WHERE kind='login_failed' AND user_id IS NOT NULL AND at>=? GROUP BY user_id",
            (_since(days),),
        ).fetchall()
    return {row["user_id"]: int(row["n"]) for row in rows}


def auth_counts(days: int = 1) -> dict[str, int]:
    with connect() as con:
        rows = con.execute(
            "SELECT kind, COUNT(*) AS n FROM auth_events WHERE at>=? GROUP BY kind", (_since(days),)
        ).fetchall()
    return {row["kind"]: int(row["n"]) for row in rows}


def prune_auth_events(days: int | None = None) -> int:
    """Drop rows older than the retention; cheap enough to run every scheduler tick."""
    cutoff = _since(days or retention_days())
    with connect() as con:
        return con.execute("DELETE FROM auth_events WHERE at<?", (cutoff,)).rowcount
