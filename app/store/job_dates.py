"""Vacancy date parsing; feeds use ISO and RFC formats."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from ..contracts import JobRow


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


def job_date(job: JobRow | dict) -> datetime:
    """Publication date, otherwise discovery date; feeds may use ISO or RFC dates."""
    for raw in (job["posted_at"], job["first_seen"]):
        parsed = parse_job_date(raw)
        if parsed:
            return parsed
    return datetime.min.replace(tzinfo=UTC)
