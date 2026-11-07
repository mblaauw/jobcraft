"""SQLite usage operations."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

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



def daily_usage_calls(kind: str, owner: str | None = None) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with connect() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM usage_log WHERE owner=? AND kind=? AND created_at >= ?",
            (owner or tenancy.user_id(), kind, start),
        ).fetchone()
    return int(row["n"])


def daily_usage_cost(owner: str | None = None) -> float:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS cost FROM usage_log "
            "WHERE owner=? AND created_at >= ?",
            (owner or tenancy.user_id(), start),
        ).fetchone()
    return float(row["cost"] or 0.0)


def daily_usage_tokens(owner: str | None = None) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with connect() as con:
        row = con.execute(
            # Jev decisions cost a fraction of a cent per thousand; a full pass over all
            # vacancies must not use up the day's room for CVs and letters.
            "SELECT COALESCE(SUM(input_tokens+output_tokens),0) AS tokens FROM usage_log "
            "WHERE owner=? AND created_at >= ? AND kind <> 'systemone'",
            (owner or tenancy.user_id(), start),
        ).fetchone()
    return int(row["tokens"])


def usage_today_per_owner() -> list[dict]:
    """Per account today: the tokens its daily limit counts (Jev left out) and its Jev calls,
    so the overview measures each limit against what that limit actually counts."""
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with connect() as con:
        rows = con.execute(
            "SELECT owner, "
            "COALESCE(SUM(CASE WHEN kind <> 'systemone' THEN input_tokens+output_tokens END),0) AS tokens, "
            "SUM(kind = 'systemone') AS jev FROM usage_log WHERE created_at >= ? GROUP BY owner",
            (start,),
        ).fetchall()
    return [{"owner": row["owner"], "tokens": int(row["tokens"]), "jev": int(row["jev"] or 0)} for row in rows]


def usage_overview(days: int = 14) -> dict:
    """Totals for the whole installation; never split per person."""
    start = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with connect() as con:
        by_day = con.execute(
            "SELECT substr(created_at,1,10) AS day, COUNT(*) AS calls, "
            "COALESCE(SUM(input_tokens+output_tokens),0) AS tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost FROM usage_log WHERE created_at >= ? "
            "GROUP BY day ORDER BY day DESC",
            (start,),
        ).fetchall()
        by_kind = con.execute(
            "SELECT kind, COUNT(*) AS calls, "
            "COALESCE(SUM(input_tokens+output_tokens),0) AS tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost FROM usage_log WHERE created_at >= ? "
            "GROUP BY kind ORDER BY tokens DESC",
            (start,),
        ).fetchall()
    return {"days": [dict(row) for row in by_day], "kinds": [dict(row) for row in by_kind]}
