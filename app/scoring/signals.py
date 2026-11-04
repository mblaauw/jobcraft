"""Signal decoding, account travel snapshots and shared exclusion rules."""

from __future__ import annotations

import hashlib
import json

from .. import geo, store
from ..contracts import JobRow
from .route import HANDLED_STATUSES


def signals_of(job: JobRow) -> dict | None:
    try:
        raw = job["typed_signals"]
    except (IndexError, KeyError):
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _job_value(job, key):
    try:
        return job[key]
    except (KeyError, IndexError):
        return None


def _home_key(settings: dict) -> str:
    raw = json.dumps({"version": geo.TRAVEL_VERSION, "home": settings}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _assess(job, settings: dict) -> dict:
    return geo.assess(
        _job_value(job, "location"),
        _job_value(job, "lat"),
        _job_value(job, "lon"),
        description=_job_value(job, "description"),
        title=_job_value(job, "title"),
        home_settings=settings,
    )


def travel_of(job: JobRow) -> dict:
    settings = geo.home()
    home_key = _home_key(settings)
    job_id = _job_value(job, "id")
    key = store.travel_key(home_key, {name: _job_value(job, name) for name in store.TRAVEL_FIELDS})
    if job_id is not None:
        same_row = _job_value(job, "travel_key") == key
        cached = _job_value(job, "travel_json") if same_row else store.get_job_travel(job_id, key)
        if cached:
            try:
                return json.loads(cached)
            except ValueError:
                pass
    result = _assess(job, settings)
    if job_id is not None:
        store.set_job_travel(job_id, key, json.dumps(result, ensure_ascii=False))
    return result


def refresh_travel() -> int:
    """Bring every stored travel estimate up to date, so lists can filter on them in SQL."""
    settings = geo.home()
    home_key = _home_key(settings)
    entries = {
        job["id"]: (
            store.travel_key(home_key, job),
            json.dumps(_assess(job, settings), ensure_ascii=False),
        )
        for job in store.jobs_missing_travel(home_key)
    }
    store.set_job_travels(entries)
    return len(entries)


def excluded_from_matching(job: JobRow) -> bool:
    """One shared skip rule for the Jev gate and LLM escalation."""
    if job["vote"] or job["status"] in HANDLED_STATUSES:
        return True
    travel = travel_of(job)
    return travel.get("in_range") is False and not travel.get("auto")
