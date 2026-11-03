"""Why a vacancy does (not) fit: asked for the moment someone opens it.

The matching round only reaches the most promising vacancies each day, so most have no
full assessment. Opening one says it matters to this person: the worker then assesses
just that vacancy, and the page swaps the explanation in when it is ready.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from . import feedback, matching, profile, ranking, runtime, store, tasks, tenancy
from .ai_budget import budget_reached

log = logging.getLogger("jobcraft")

KIND = "verdict"
STALE_SECONDS = 5 * 60
RETRY_AFTER_SECONDS = 24 * 3600  # a failed answer is not asked again on every visit


def _key(job_id: int) -> str:
    return f"verdict:{tenancy.user_id()}:{job_id}"


def _age(moment: str | None) -> float:
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(moment or "")).total_seconds()
    except ValueError:
        return float("inf")


def state(job) -> dict:
    """{"pending": bool, "since": iso, "failed": bool} for the latest request of this vacancy."""
    task = tasks.latest(KIND, owner=tenancy.user_id(), key=_key(job["id"]))
    if not task or job["match_score"] is not None:
        return {"pending": False, "since": "", "failed": False}
    since = task.get("started_at") or task.get("created_at") or ""
    pending = task["status"] in ("queued", "running") and _age(task.get("created_at")) < STALE_SECONDS
    failed = task["status"] == "error" and _age(task.get("finished_at")) < RETRY_AFTER_SECONDS
    return {"pending": pending, "since": since, "failed": failed}


def request(job) -> bool:
    """Ask for an assessment of this vacancy when it has none. True when one is on its way."""
    if job["match_score"] is not None or job["status"] == "ignored":
        return False
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return False
    if getattr(runtime.manager, "client", None) is None or budget_reached():
        return False
    current = state(job)
    if current["pending"]:
        return True
    if current["failed"]:
        return False
    owner = tenancy.user_id()
    tasks.enqueue(KIND, owner=owner, payload={"job_id": job["id"]}, dedupe_key=_key(job["id"]))
    return True


def context(job) -> dict:
    """What the "Waarom dit (niet) past" part of the page shows."""
    reason = None
    if job["match_reason"]:
        try:
            reason = json.loads(job["match_reason"])
        except ValueError:
            reason = {"why": job["match_reason"]}
    asked = request(job)
    return {"reason": reason, "verdict": {**state(job), "asked": asked}}


@tasks.handler(KIND)
def _task(task):
    from . import auth

    user = auth._get_user_by_id(task["owner"]) if task["owner"] else None
    if user is None:
        raise RuntimeError("unknown task owner")
    job_id = int(json.loads(task["payload_json"])["job_id"])
    with tenancy.scope(
        {"id": user["id"], "username": user["username"], "display_name": user["display_name"], "role": user["role"]}
    ):
        job = store.get_job(job_id)
        if job is None or job["match_score"] is not None:
            return {}
        preferences = profile.read_preferences()
        profile_md = profile.read_public_excerpt(matching.PROFILE_PROMPT_LIMIT * 2)
        digest = matching.current_digest(preferences, profile_md)
        score = matching.judge(runtime.get_manager(), job, preferences, profile_md, feedback.load(), digest)
        ranking.after_verdict(job_id, score)
    return {"score": score}
