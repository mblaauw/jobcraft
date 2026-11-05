"""Counts for the reach slider: how many vacancies each stop would show."""

from __future__ import annotations

from .. import geo, tenancy
from .connection import connect
from .jobs import _job_filters, _unique_jobs


def reach_counts(
    reaches: list[dict],
    status: str | None = None,
    query_text: str | None = None,
    source: str | None = None,
    include_stale: bool = False,
    list_filters: dict | None = None,
) -> list[int]:
    """Unique vacancies not too far, for each reach; one pass over the list's own filters."""
    clauses, filters = _job_filters(None, query_text, source, include_stale, False, list_filters)
    sums, params = [], []
    for reach in reaches:
        far_sql, far_params = geo.too_far_sql(reach)
        sums.append(f"COALESCE(SUM(NOT {far_sql}),0)")
        params.extend(far_params)
    sql = "SELECT " + ", ".join(sums) + " FROM (" + _unique_jobs(clauses) + ")"
    if status:
        sql += " WHERE status=?"
    with connect() as con:
        row = con.execute(sql, (*params, tenancy.user_id(), *filters, *([status] if status else []))).fetchone()
    return [int(value) for value in row]
