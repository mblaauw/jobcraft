"""The one walk over an account's live vacancies that ranking, System One and routing share."""

from __future__ import annotations

import logging

from .. import tenancy
from .connection import connect, stale_cutoff
from .jobs import _disabled_user_sources, _job_select, import_source

log = logging.getLogger("jobcraft")

# A memory safety net, not a working limit. The walk is newest first, so a cap that
# binds leaves the oldest live vacancies unranked and unassessed. In the worker 28,318
# rows peak near 760 MB (2026-11-14); keep full walks out of the 768 MB web process.
MATCHING_LIMIT = 60000
# A pass that must run inside a web request keeps the newest this many (about 600 MB).
WEB_WALK_LIMIT = 20000


def list_jobs_for_matching(limit: int | None = None) -> list:
    disabled = _disabled_user_sources()
    params: list = [tenancy.user_id(), stale_cutoff(), import_source(), *disabled]
    source_sql = (
        " AND (source_id IS NULL OR source_id NOT IN (" + ",".join("?" for _ in disabled) + "))"
        if disabled
        else ""
    )
    cap = limit or MATCHING_LIMIT
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM ("
            + _job_select()
            + ") WHERE NOT (last_seen < ? AND status NOT IN ('saved','applied','interview','offer','rejected') "
            "AND EXISTS (SELECT 1 FROM sources src WHERE src.id=source_id "
            "AND src.last_status='ok' AND src.last_scan_at > last_seen)) "
            "AND (source_id IS NULL OR source_id NOT LIKE 'import:%' OR source_id=?)"
            + source_sql
            + " ORDER BY COALESCE(posted_at, first_seen) DESC, id DESC LIMIT ?",
            (*params, cap + 1),  # one more, to know whether the cap left anything out
        ).fetchall()
    if len(rows) > cap and limit is None:
        log.warning("matching walk capped at %d vacancies: older live ones are left out", cap)
    return rows[:cap] if len(rows) > cap else rows
