"""Deterministic ranking on top of the rule score, System One signals and votes.

The important property: this module can re-rank every job in milliseconds, so a
vote never invalidates an expensive verdict. Feedback only moves the rule score;
the Jev/LLM verdicts stay cached.
"""

from __future__ import annotations

import hashlib
import json
import os

from . import feedback, geo, matching, profile, store, tenancy

MIN_RANK_FOR_LLM = float(os.environ.get("MATCH_MIN_RANK", "35"))


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
    raw = "|".join(
        str(value or "")
        for value in (
            _job_value(job, "location"),
            _job_value(job, "lat"),
            _job_value(job, "lon"),
            _job_value(job, "description"),
            _job_value(job, "title"),
            settings.get("label"),
            settings.get("bike_minutes"),
            settings.get("transit_minutes"),
        )
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


def effective_fit(signals: dict | None) -> float | None:
    """Shrink the Jev fit toward neutral when its confidence is low."""
    if not signals or signals.get("fit") is None:
        return None
    confidence = max(0.0, min(1.0, float(signals.get("confidence") or 0.0)))
    return round(50.0 + (float(signals["fit"]) - 50.0) * confidence, 1)


def verdict_is_fresh(job, digest_base: str) -> bool:
    """The stored hash is a per-job digest, not the base profile digest."""
    return job["match_score"] is not None and job["profile_hash"] == matching.job_match_digest(
        digest_base, job
    )


def fit_calibration(jobs, digest: str | None) -> tuple[float, float]:
    """Least-squares map from Jev fit to the LLM verdict scale.

    Jev answers a different question than the LLM, so its 0-100 scale sits lower
    (measured mean 14 vs 30). Calibrating before blending keeps the ranking from
    being decided by *which* judge happened to see a job.
    """
    pairs: list[tuple[float, float]] = []
    for job in jobs:
        signals = signals_of(job)
        if not signals or signals.get("fit") is None:
            continue
        if digest is not None and verdict_is_fresh(job, digest):
            pairs.append((float(signals["fit"]), float(job["match_score"])))
    if len(pairs) < 20:
        return (1.0, 0.0)
    n = len(pairs)
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    variance = sum((x - mean_x) ** 2 for x, _ in pairs)
    if variance < 1e-6:
        return (1.0, 0.0)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in pairs) / variance
    slope = max(0.2, min(3.0, slope))
    return (round(slope, 3), round(mean_y - slope * mean_x, 2))


def calibrated_fit(signals: dict | None, calibration: tuple[float, float]) -> float | None:
    fit = effective_fit(signals)
    if fit is None:
        return None
    slope, intercept = calibration
    return round(max(0.0, min(100.0, slope * fit + intercept)), 1)


def rank_for(
    job,
    preferences: dict,
    feedback_data: dict,
    signals: dict | None = None,
    digest: str | None = None,
    calibration: tuple[float, float] = (1.0, 0.0),
) -> tuple[float, dict]:
    rule, notes = matching.rule_score(job, preferences, feedback_data)
    fit = calibrated_fit(signals, calibration)
    llm = None
    if digest is not None and verdict_is_fresh(job, digest):
        llm = float(job["match_score"])
    # Renormalized weights: components with different scales do not distort the
    # blend just because one is missing for a given job.
    components: list[tuple[float, float]] = [(0.35, rule)]
    if fit is not None:
        components.append((0.40, fit))
    if llm is not None:
        components.append((0.25, llm))
    weight = sum(component[0] for component in components)
    rank = sum(component[0] * component[1] for component in components) / weight
    return round(max(0.0, min(100.0, rank)), 1), {
        "rule": round(rule, 1),
        "fit": fit,
        "llm": llm,
        "notes": notes,
    }


def rerank_one(job_id: int) -> float | None:
    """Cheap immediate re-rank for one voted job; the full pass runs separately."""
    job = store.get_job(job_id)
    if not job:
        return None
    prefs = profile.read_preferences()
    data = feedback.load()
    digest = matching.current_digest()
    rank, _ = rank_for(job, prefs, data, signals_of(job), digest)
    store.set_rank_scores({job_id: rank})
    return rank


def rerank_all(
    preferences: dict | None = None,
    feedback_data: dict | None = None,
    digest: str | None = None,
) -> int:
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return 0
    prefs = preferences if preferences is not None else profile.read_preferences()
    data = feedback_data if feedback_data is not None else feedback.load()
    digest = digest or matching.current_digest(prefs)
    jobs = store.list_jobs_for_matching(2000)
    calibration = fit_calibration(jobs, digest)
    store.meta_set(
        "fit_calibration",
        json.dumps({"slope": calibration[0], "intercept": calibration[1]}),
    )
    scores: dict[int, float] = {}
    for job in jobs:
        rank, _ = rank_for(job, prefs, data, signals_of(job), digest, calibration)
        scores[job["id"]] = rank
    store.set_rank_scores(scores)
    store.meta_set("queue_depth_digest", "")
    depth = len(escalation_candidates(digest, 10000))
    store.meta_set("queue_depth", str(depth))
    store.meta_set("queue_depth_digest", digest)
    return len(scores)


def escalation_candidates(
    digest: str,
    limit: int,
    preferences: dict | None = None,
    feedback_data: dict | None = None,
) -> list:
    """Jobs worth an expensive LLM verdict: promising, unvoted, uncertain."""
    prefs = preferences if preferences is not None else profile.read_preferences()
    data = feedback_data if feedback_data is not None else feedback.load()
    candidates: list[tuple[float, object]] = []
    for job in store.list_jobs_for_matching(2000):
        if excluded_from_matching(job):
            continue
        if verdict_is_fresh(job, digest):
            continue
        signals = signals_of(job)
        rank = (
            job["rank_score"]
            if job["rank_score"] is not None
            else rank_for(job, prefs, data, signals, digest)[0]
        )
        if rank < MIN_RANK_FOR_LLM:
            continue
        if signals is not None and not signals.get("escalate"):
            continue
        candidates.append((rank, job))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [job for _, job in candidates[:limit]]


def queue_depth(digest: str) -> int:
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return 0
    if store.meta_get("queue_depth_digest") == digest:
        cached = store.meta_get("queue_depth")
        if cached is not None:
            return int(cached)
    depth = len(escalation_candidates(digest, 10000))
    store.meta_set("queue_depth", str(depth))
    store.meta_set("queue_depth_digest", digest)
    return depth
