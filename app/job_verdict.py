"""Why a vacancy does (not) fit: asked for the moment someone opens it, when the route says so.

The matching round only reaches the most promising vacancies each day, so most have no
full assessment. Opening one says it matters to this person. The route then decides: a
vacancy Jev is sure about is explained from the quick assessment and buys nothing, one
without a quick assessment asks Jev first, and only the unsure ones go to the matcher.
"Beoordeel dieper" is the person's own escalation and always wins.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from . import feedback, matching, profile, ranking, runtime, store, systemone, tasks, tenancy
from .ai_budget import budget_reached
from .scoring.route import Route

log = logging.getLogger("jobcraft")

KIND = "verdict"
STALE_SECONDS = 5 * 60
RETRY_AFTER_SECONDS = 24 * 3600  # a failed answer is not asked again on every visit
WANTED_PREFIX = "verdict_wanted:"


def _key(job_id: int) -> str:
    return f"verdict:{tenancy.user_id()}:{job_id}"


def _age(moment: str | None) -> float:
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(moment or "")).total_seconds()
    except ValueError:
        return float("inf")


def wanted(job_id: int) -> bool:
    return store.meta_get(f"{WANTED_PREFIX}{job_id}") == "1"


def _set_wanted(job_id: int, flag: bool) -> None:
    store.meta_set(f"{WANTED_PREFIX}{job_id}", "1" if flag else "")


def route_for(job, digest: str | None = None) -> Route:
    """What this vacancy needs next for this person, the person's own wish included."""
    return ranking.route_of(job, digest or matching.current_digest(), wanted=wanted(job["id"]))


def _can_ask() -> bool:
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return False
    return getattr(runtime.manager, "client", None) is not None and not budget_reached()


def state(job) -> dict:
    """{"pending": bool, "since": iso, "failed": bool} for the latest request of this vacancy."""
    task = tasks.latest(KIND, owner=tenancy.user_id(), key=_key(job["id"]))
    if not task or job["match_score"] is not None:
        return {"pending": False, "since": "", "failed": False}
    since = task.get("started_at") or task.get("created_at") or ""
    pending = task["status"] in ("queued", "running") and _age(task.get("created_at")) < STALE_SECONDS
    failed = task["status"] == "error" and _age(task.get("finished_at")) < RETRY_AFTER_SECONDS
    return {"pending": pending, "since": since, "failed": failed}


def _enqueue(job, retry: bool = False) -> bool:
    current = state(job)
    if current["pending"]:
        return True
    if current["failed"] and not retry:
        return False
    owner = tenancy.user_id()
    tasks.enqueue(KIND, owner=owner, payload={"job_id": job["id"]}, dedupe_key=_key(job["id"]))
    return True


def request(job, decision: Route | None = None) -> bool:
    """Ask for what the route says this vacancy needs. True when work is on its way."""
    if job["match_score"] is not None or not _can_ask():
        return False
    decision = decision or route_for(job)
    if decision.outcome not in ("wait", "escalate"):
        return False
    return _enqueue(job)


def deeper(job) -> bool:
    """The person asks for the full assessment; remembered until a verdict lands.

    A failure earlier today does not stand in the way: this is an explicit retry.
    """
    if job["match_score"] is not None or not _can_ask():
        return False
    _set_wanted(job["id"], True)
    store.record_job_event(job["id"], "deeper", "asked")
    return _enqueue(job, retry=True)


def context(job) -> dict:
    """What the "Waarom dit (niet) past" part of the page shows."""
    reason = None
    if job["match_reason"]:
        try:
            reason = json.loads(job["match_reason"])
        except ValueError:
            reason = {"why": job["match_reason"]}
    decision = None if job["match_score"] is not None else route_for(job)
    asked = request(job, decision) if decision else False
    return {
        "reason": reason,
        "verdict": {**state(job), "asked": asked},
        "route": decision,
        "grade": systemone.fit_grade(ranking.signals_of(job)),
        "can_deepen": decision is not None and decision.outcome == "act" and _can_ask(),
    }


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
        decision = route_for(job, digest)
        if decision.outcome == "wait":
            # Jev first: about a second, and often the last word.
            signals = matching.signal_one(dict(job), preferences, digest_base=digest)
            if signals is not None:
                ranking.after_signals(job_id, signals)
                job = store.get_job(job_id)
                decision = route_for(job, digest)
        if decision.outcome in ("act", "excluded", "verdict"):
            return {"route": decision.outcome, "code": decision.code}
        if budget_reached():  # the limit may have been crossed since the page asked
            return {"route": "budget", "code": decision.code}
        score = matching.judge(runtime.get_manager(), job, preferences, profile_md, feedback.load(), digest)
        ranking.after_verdict(job_id, score)
        _set_wanted(job_id, False)
    return {"score": score, "route": "verdict", "code": decision.code}
