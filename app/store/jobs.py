"""SQLite jobs operations."""

from __future__ import annotations

import json
import sqlite3

from .. import config, geo, source_policy, tenancy
from ..contracts import JobRow
from .accounts import get_preferences
from .connection import connect, stale_cutoff
from .job_dates import job_date as job_date
from .job_dates import normalize_posted_at as normalize_posted_at
from .job_dates import parse_job_date as parse_job_date
from .job_list_filters import list_filter_clauses
from .sources import list_sources, list_user_sources, load_source_configs

# One key format for Python and SQL, so a whole list can be checked in one query.
TRAVEL_KEY_SQL = (
    "(? || ':' || COALESCE(j.content_hash,'') || ':' || COALESCE(j.location,'') "
    "|| ':' || CASE WHEN j.lat IS NULL THEN '' ELSE printf('%.6f',j.lat) END "
    "|| ':' || CASE WHEN j.lon IS NULL THEN '' ELSE printf('%.6f',j.lon) END)"
)


TRAVEL_FIELDS = ("content_hash", "location", "lat", "lon")


def travel_key(home_key: str, job: JobRow | dict) -> str:
    """Python twin of TRAVEL_KEY_SQL."""
    def coordinate(value: float | None) -> str:
        return "" if value is None else f"{float(value):.6f}"

    return ":".join(
        [
            home_key,
            job["content_hash"] or "",
            job["location"] or "",
            coordinate(job["lat"]),
            coordinate(job["lon"]),
        ]
    )


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
    fields.extend(["s.travel_key AS travel_key", "s.travel_json AS travel_json"])
    return (
        "SELECT "
        + ",".join(fields)
        + " FROM jobs j LEFT JOIN job_states s ON s.job_id=j.id AND s.owner=?"
    )


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


def import_source(owner: str | None = None) -> str:
    return f"import:{owner or tenancy.user_id()}"


# The same vacancy on several sites counts once; the one you acted on stays visible.
_UNIQUE_JOBS = """SELECT * FROM (
  SELECT id, status, vote, rank_score, match_score, posted_at, first_seen, travel_json,
    ROW_NUMBER() OVER (PARTITION BY dup_key ORDER BY status='new', vote=0,
      COALESCE(rank_score,-1) DESC, id) AS dup_rank,
    COUNT(*) OVER (PARTITION BY dup_key) AS dup_count
  FROM (SELECT v.id, v.status, v.vote, v.rank_score, v.match_score, v.posted_at, v.first_seen,
      v.travel_json, CASE WHEN TRIM(COALESCE(company,''))='' THEN 'id:'||id
      ELSE LOWER(TRIM(title))||'|'||LOWER(TRIM(company))||'|'||
        LOWER(COALESCE(json_extract(travel_json,'$.place'),COALESCE(location,'')))
      END AS dup_key FROM ({select}) v {where})
) WHERE dup_rank=1"""


def _unique_jobs(clauses: list[str]) -> str:
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return _UNIQUE_JOBS.format(select=_job_select(), where=where)


MAX_SEARCH_WORDS = 8


def _job_filters(
    status: str | None = None,
    query_text: str | None = None,
    source: str | None = None,
    include_stale: bool = False,
    only_unvoted: bool = False,
    list_filters: dict | None = None,
) -> tuple[list[str], list]:
    clauses, params = list_filter_clauses(list_filters)
    if status:
        clauses.append("status=?")
        params.append(status)
    if only_unvoted:
        clauses.append("COALESCE(vote, 0)=0")
    if source:
        clauses.append("source_id=?")
        params.append(source)
    # Every word must appear, in any order and also inside Dutch compounds.
    for word in (query_text or "").lower().split()[:MAX_SEARCH_WORDS]:
        escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("search_text LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    # Pasted vacancies are private: the jobs table itself is shared between accounts.
    clauses.append("(source_id IS NULL OR source_id NOT LIKE 'import:%' OR source_id=?)")
    params.append(import_source())
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
            "NOT (last_seen < ? AND status NOT IN ('saved','applied','interview','offer','rejected') "
            "AND EXISTS (SELECT 1 FROM sources src WHERE src.id=source_id "
            "AND src.last_status='ok' AND src.last_scan_at > last_seen))"
        )
        params.append(stale_cutoff())
    return clauses, params


def _job_order(sort: str) -> str:
    # Only internal SQL fragments, never user-supplied column names.
    if sort == "newest":
        return " ORDER BY COALESCE(posted_at, first_seen) DESC, id DESC"
    return (
        " ORDER BY COALESCE(rank_score, match_score, -1) DESC, "
        "COALESCE(posted_at, first_seen) DESC, id DESC"
    )


def list_jobs(
    limit: int | None = 200,
    status: str | None = None,
    query_text: str | None = None,
    source: str | None = None,
    sort: str = "rank",
    include_stale: bool = False,
    only_unvoted: bool = False,
) -> list:
    clauses, filters = _job_filters(status, query_text, source, include_stale, only_unvoted)
    params: list = [tenancy.user_id(), *filters]
    sql = "SELECT * FROM (" + _job_select() + ")"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += _job_order(sort)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as con:
        return con.execute(sql, tuple(params)).fetchall()


def _too_far(reach: dict | None) -> tuple[str, list]:
    """Hide on distance by the account's own budgets, or by the ones a list was given."""
    return geo.too_far_sql(reach if reach is not None else geo.home())


def job_status_counts(
    query_text: str | None = None,
    source: str | None = None,
    include_stale: bool = False,
    list_filters: dict | None = None,
    only_unvoted: bool = False,
    reach: dict | None = None,
) -> dict[str, tuple[int, int]]:
    """Per status: (unique jobs, unique jobs known to be outside the travel range)."""
    clauses, filters = _job_filters(
        None, query_text, source, include_stale, only_unvoted, list_filters
    )
    far_sql, far_params = _too_far(reach)
    sql = (
        f"SELECT status, COUNT(*) AS n, COALESCE(SUM({far_sql}),0) AS far FROM ("
        + _unique_jobs(clauses)
        + ") GROUP BY status"
    )
    with connect() as con:
        rows = con.execute(sql, (*far_params, tenancy.user_id(), *filters)).fetchall()
    return {row["status"]: (row["n"], row["far"]) for row in rows}


def list_jobs_page(
    limit: int,
    offset: int = 0,
    status: str | None = None,
    query_text: str | None = None,
    source: str | None = None,
    sort: str = "rank",
    include_stale: bool = False,
    within_travel: bool | dict = False,
    only_unvoted: bool = False,
    list_filters: dict | None = None,
) -> list:
    """One page of full rows; ordering touches no descriptions, so it stays cheap at scale."""
    clauses, filters = _job_filters(
        None, query_text, source, include_stale, only_unvoted, list_filters
    )
    outer: list[str] = []
    outer_params: list = []
    if status:
        outer.append("status=?")
        outer_params.append(status)
    if within_travel:
        # True: the account's own reach; a dict: budgets this list was given.
        far_sql, far_params = _too_far(within_travel if isinstance(within_travel, dict) else None)
        outer.append("NOT " + far_sql)
        outer_params.extend(far_params)
    sql = "SELECT id, dup_count FROM (" + _unique_jobs(clauses) + ")"
    if outer:
        sql += " WHERE " + " AND ".join(outer)
    sql += _job_order(sort) + " LIMIT ? OFFSET ?"
    with connect() as con:
        picked = con.execute(
            sql, (tenancy.user_id(), *filters, *outer_params, limit, offset)
        ).fetchall()
        ids = [row["id"] for row in picked]
        if not ids:
            return []
        rows = con.execute(
            "SELECT * FROM ("
            + _job_select()
            + ") WHERE id IN ("
            + ",".join("?" for _ in ids)
            + ")",
            (tenancy.user_id(), *ids),
        ).fetchall()
    by_id = {row["id"]: {**dict(row), "dup_count": 1} for row in rows}
    for row in picked:
        if row["id"] in by_id:
            by_id[row["id"]]["dup_count"] = row["dup_count"]
    return [by_id[job_id] for job_id in ids if job_id in by_id]


def count_catalogue(include: list[str], exclude: list[str], exclude_titles: list[str]) -> int:
    """How many reachable jobs could fit; instant enough to follow each tap."""
    clauses, filters = _job_filters()
    far_sql, far_params = _too_far(None)
    clauses.append("NOT " + far_sql)
    filters.extend(far_params)
    if include:
        clauses.append("(" + " OR ".join("search_text LIKE ?" for _ in include) + ")")
        filters.extend(f"%{term.lower()}%" for term in include)
    for term in exclude:
        clauses.append("search_text NOT LIKE ?")
        filters.append(f"%{term.lower()}%")
    for term in exclude_titles:
        clauses.append("LOWER(title) NOT LIKE ?")
        filters.append(f"%{term.lower()}%")
    sql = "SELECT COUNT(*) AS n FROM (" + _job_select() + ") WHERE " + " AND ".join(clauses)
    with connect() as con:
        return con.execute(sql, (tenancy.user_id(), *filters)).fetchone()["n"]


def jobs_missing_travel(home_key: str) -> list:
    """Jobs whose stored travel estimate belongs to another home or vacancy version."""
    with connect() as con:
        return con.execute(
            "SELECT j.id,j.location,j.lat,j.lon,j.description,j.title,j.content_hash FROM jobs j "
            "LEFT JOIN job_states s ON s.job_id=j.id AND s.owner=? "
            "WHERE s.travel_key IS NULL OR s.travel_key <> "
            f"{TRAVEL_KEY_SQL}",
            (tenancy.user_id(), home_key),
        ).fetchall()


def get_job(job_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM ("
            + _job_select()
            + ") WHERE id=? AND (source_id IS NULL OR source_id NOT LIKE 'import:%' OR source_id=?)",
            (tenancy.user_id(), job_id, import_source()),
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


MATCHING_LIMIT = 20000


def list_jobs_for_matching(limit: int = MATCHING_LIMIT) -> list:
    disabled = _disabled_user_sources()
    source_sql = ""
    params: list = [tenancy.user_id(), stale_cutoff(), import_source()]
    if disabled:
        source_sql = (
            " AND (source_id IS NULL OR source_id NOT IN (" + ",".join("?" for _ in disabled) + "))"
        )
        params.extend(disabled)
    with connect() as con:
        return con.execute(
            "SELECT * FROM ("
            + _job_select()
            + ") WHERE NOT (last_seen < ? AND status NOT IN ('saved','applied','interview','offer','rejected') "
            "AND EXISTS (SELECT 1 FROM sources src WHERE src.id=source_id "
            "AND src.last_status='ok' AND src.last_scan_at > last_seen)) "
            "AND (source_id IS NULL OR source_id NOT LIKE 'import:%' OR source_id=?)"
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
