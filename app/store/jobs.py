"""SQLite jobs operations."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .. import config, source_policy, tenancy
from ..contracts import JobRow
from .accounts import get_preferences
from .connection import connect, stale_cutoff
from .sources import list_sources, list_user_sources, load_source_configs


def _job_select(owner: str | None = None) -> str:
    owner = owner or tenancy.user_id()
    public = [
        "id",
        "source_id",
        "url",
        "title",
        "company",
        "location",
        "hours",
        "description",
        "posted_at",
        "first_seen",
        "last_seen",
        "content_hash",
        "lat",
        "lon",
        "search_text",
    ]
    defaults = {
        "status": "'new'",
        "vote": "0",
        "match_score": "NULL",
        "match_reason": "NULL",
        "profile_hash": "NULL",
        "rank_score": "NULL",
        "typed_signals": "NULL",
        "signals_key": "NULL",
        "signal_confidence": "NULL",
    }
    fields = ["j." + name for name in public]
    for name, default in defaults.items():
        fallback = "j." + name if owner == tenancy.LEGACY_ID else default
        fields.append(f"COALESCE(s.{name},{fallback}) AS {name}")
    return (
        "SELECT "
        + ",".join(fields)
        + " FROM jobs j LEFT JOIN job_states s ON s.job_id=j.id AND s.owner=?"
    )


def parse_job_date(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    try:
        try:
            parsed = datetime.fromisoformat(str(raw).strip())
        except ValueError:
            parsed = parsedate_to_datetime(str(raw).strip())
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_posted_at(raw: str | None) -> str | None:
    """Store one comparable ISO-8601 UTC form; absent or invalid becomes NULL."""
    parsed = parse_job_date(raw)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def job_date(job: JobRow) -> datetime:
    """Publication date, otherwise discovery date; feeds may use ISO or RFC dates."""
    for raw in (job["posted_at"], job["first_seen"]):
        parsed = parse_job_date(raw)
        if parsed:
            return parsed
    return datetime.min.replace(tzinfo=UTC)


def effective_user_sources() -> dict[str, dict]:
    """Same policy for lists, matching and UI; scanning activation is independent."""
    overrides = list_user_sources()
    configs = {item["id"]: item for item in load_source_configs()}
    preferences = get_preferences()
    if preferences is None:
        path = config.user_path(config.PROFILE_DIR / "preferences.json")
        preferences = json.loads(path.read_text()) if path.exists() else {}
    home = preferences.get("home") or {}
    ids = set(source_policy.seed_metadata()) | set(configs) | set(overrides)
    ids.update(row["id"] for row in list_sources())
    return {
        source_id: source_policy.selection(
            configs.get(source_id, {"id": source_id}), home, overrides.get(source_id)
        )
        for source_id in ids
    }


def _disabled_user_sources() -> list[str]:
    return [key for key, value in effective_user_sources().items() if not value["selected"]]


def list_jobs(
    limit: int | None = 200,
    status: str | None = None,
    query_text: str | None = None,
    source: str | None = None,
    sort: str = "rank",
    include_stale: bool = False,
    only_unvoted: bool = False,
) -> list:
    clauses: list[str] = []
    params: list = [tenancy.user_id()]
    if status:
        clauses.append("status=?")
        params.append(status)
    if only_unvoted:
        clauses.append("COALESCE(vote, 0)=0")
    if source:
        clauses.append("source_id=?")
        params.append(source)
    if query_text:
        like = f"%{query_text}%"
        clauses.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        params.extend([like, like, like])
    disabled_sources = _disabled_user_sources()
    if disabled_sources:
        clauses.append(
            "(source_id IS NULL OR source_id NOT IN ("
            + ",".join("?" for _ in disabled_sources)
            + "))"
        )
        params.extend(disabled_sources)
    if not include_stale:
        clauses.append(
            "NOT (last_seen < ? AND status NOT IN ('saved','applied') "
            "AND EXISTS (SELECT 1 FROM sources src WHERE src.id=source_id "
            "AND src.last_status='ok' AND src.last_scan_at > last_seen))"
        )
        params.append(stale_cutoff())
    sql = "SELECT * FROM (" + _job_select() + ")"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # Only internal SQL fragments, never user-supplied column names.
    if sort == "newest":
        sql += " ORDER BY COALESCE(posted_at, first_seen) DESC, id DESC"
    else:
        sql += " ORDER BY COALESCE(rank_score, match_score, -1) DESC, COALESCE(posted_at, first_seen) DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as con:
        return con.execute(sql, tuple(params)).fetchall()


def get_job(job_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM (" + _job_select() + ") WHERE id=?", (tenancy.user_id(), job_id)
        ).fetchone()


def list_scored_jobs(owner: str | None = None) -> list:
    """Owner-scoped rows for offline quality checks, not for request handlers."""
    owner = owner or tenancy.user_id()
    with connect() as con:
        return con.execute(
            "SELECT id, rank_score, match_score, typed_signals FROM ("
            + _job_select(owner)
            + ") WHERE match_score IS NOT NULL AND rank_score IS NOT NULL",
            (owner,),
        ).fetchall()


def list_jobs_for_matching(limit: int = 2000) -> list:
    disabled = _disabled_user_sources()
    source_sql = ""
    params: list = [tenancy.user_id(), stale_cutoff()]
    if disabled:
        source_sql = (
            " AND (source_id IS NULL OR source_id NOT IN (" + ",".join("?" for _ in disabled) + "))"
        )
        params.extend(disabled)
    with connect() as con:
        return con.execute(
            "SELECT * FROM ("
            + _job_select()
            + ") WHERE NOT (last_seen < ? AND status NOT IN ('saved','applied') "
            "AND EXISTS (SELECT 1 FROM sources src WHERE src.id=source_id "
            "AND src.last_status='ok' AND src.last_scan_at > last_seen)) "
            + source_sql
            + " ORDER BY COALESCE(posted_at, first_seen) DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()


def count_jobs() -> dict:
    with connect() as con:
        total = con.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        view = "(" + _job_select() + ")"
        fresh = con.execute(
            "SELECT COUNT(*) AS n FROM " + view + " WHERE status='new'", (tenancy.user_id(),)
        ).fetchone()["n"]
        matched = con.execute(
            "SELECT COUNT(*) AS n FROM " + view + " WHERE match_score IS NOT NULL",
            (tenancy.user_id(),),
        ).fetchone()["n"]
        signaled = con.execute(
            "SELECT COUNT(*) AS n FROM " + view + " WHERE typed_signals IS NOT NULL",
            (tenancy.user_id(),),
        ).fetchone()["n"]
    return {
        "total": total,
        "new": fresh,
        "matched": matched,
        "signaled": signaled,
        "unmatched": total - matched,
    }


def count_jobs_by_source() -> dict[str, int]:
    with connect() as con:
        rows = con.execute(
            "SELECT COALESCE(source_id, '?') AS source_id, COUNT(*) AS n FROM jobs GROUP BY source_id"
        ).fetchall()
    return {row["source_id"]: row["n"] for row in rows}
