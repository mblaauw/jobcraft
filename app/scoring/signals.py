"""Signal decoding, account travel snapshots and shared exclusion rules."""
from __future__ import annotations

import hashlib
import json

from .. import geo, store


def signals_of(job) -> dict | None:
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



def travel_of(job) -> dict:
    settings = geo.home()
    raw = json.dumps(
        {
            "version": geo.TRAVEL_VERSION,
            "home": settings,
            "job": {
                key: _job_value(job, key)
                for key in ("location", "lat", "lon", "description", "title")
            },
        },
        sort_keys=True,
    )
    key = hashlib.sha256(raw.encode()).hexdigest()[:20]
    job_id = job["id"] if "id" in job.keys() else None
    if job_id is not None:
        cached = store.get_job_travel(job_id, key)
        if cached:
            try:
                return json.loads(cached)
            except ValueError:
                pass
    result = geo.assess(
        _job_value(job, "location"),
        _job_value(job, "lat"),
        _job_value(job, "lon"),
        description=_job_value(job, "description"),
        title=_job_value(job, "title"),
        home_settings=settings,
    )
    if job_id is not None:
        store.set_job_travel(job_id, key, json.dumps(result, ensure_ascii=False))
    return result



def excluded_from_matching(job) -> bool:
    """One shared skip rule for the Jev gate and LLM escalation."""
    if job["vote"] or job["status"] in ("ignored", "applied"):
        return True
    travel = travel_of(job)
    return travel.get("in_range") is False and not travel.get("auto")

