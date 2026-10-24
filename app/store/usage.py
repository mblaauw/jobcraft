"""SQLite usage operations."""
from __future__ import annotations

from datetime import UTC, datetime

from .. import tenancy
from .connection import connect, now


def record_usage(
    session_id: str,
    kind: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    agent: str | None = None,
) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO usage_log"
            "(session_id, kind, input_tokens, output_tokens, cost_usd, created_at, agent,owner) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                session_id,
                kind,
                input_tokens,
                output_tokens,
                cost_usd,
                now(),
                agent,
                tenancy.user_id(),
            ),
        )



def usage_total() -> dict:
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS tin, "
            "COALESCE(SUM(output_tokens),0) AS tout, "
            "COALESCE(SUM(cost_usd),0) AS cost, COUNT(*) AS calls FROM usage_log WHERE owner=?",
            (tenancy.user_id(),),
        ).fetchone()
    return dict(row)



def daily_usage_cost(owner: str | None = None) -> float:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS cost FROM usage_log "
            "WHERE owner=? AND created_at >= ?",
            (owner or tenancy.user_id(), start),
        ).fetchone()
    return float(row["cost"] or 0.0)

