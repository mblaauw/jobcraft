"""Deterministic ranking on top of the rule score, System One signals and votes.

The important property: this module can re-rank every job in milliseconds, so a
vote never invalidates an expensive verdict. Feedback only moves the rule score;
the Jev/LLM verdicts stay cached.
"""

from __future__ import annotations

import json
import os

from . import feedback, profile, store, taste, tenancy
from . import geo as geo
from .scoring.digests import current_digest as _current_digest
from .scoring.digests import job_match_digest
from .scoring.rules import rule_score
from .scoring.signals import excluded_from_matching, signals_of
from .scoring.signals import refresh_travel as refresh_travel
from .scoring.signals import travel_of as travel_of
from .scoring.weights import read_weights

MIN_RANK_FOR_LLM = float(os.environ.get("MATCH_MIN_RANK", "35"))


def effective_fit(signals: dict | None) -> float | None:
    """Shrink the Jev fit toward neutral when its confidence is low."""
    if not signals or signals.get("fit") is None:
        return None
    confidence = max(0.0, min(1.0, float(signals.get("confidence") or 0.0)))
    return round(50.0 + (float(signals["fit"]) - 50.0) * confidence, 1)


def verdict_is_fresh(job, digest_base: str) -> bool:
    """The stored hash is a per-job digest, not the base profile digest."""
    return job["match_score"] is not None and job["profile_hash"] == job_match_digest(
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


PARTS_KEY = "rank_parts"


def _vote(job) -> int:
    try:  # sqlite rows and plain dicts; a row without a vote counts as none
        return int(job["vote"] or 0)
    except (KeyError, IndexError):
        return 0


def blend(
    rule: float,
    fit: float | None,
    llm: float | None,
    taste_score: float | None,
    vote: int,
    weights=None,
) -> float:
    """Renormalized weights, so a missing part does not distort the rest; your own vote wins."""
    weights = weights or read_weights()
    components: list[tuple[float, float]] = [(weights.rule, rule)]
    for weight, value in ((weights.fit, fit), (weights.llm, llm), (weights.taste, taste_score)):
        if value is not None:
            components.append((weight, value))
    scale = max(weight for weight, _ in components)  # huge weights must not overflow
    components = [(weight / scale, value) for weight, value in components] if scale else []
    total = sum(weight for weight, _ in components)
    rank = sum(weight * value for weight, value in components) / total if total else rule
    if vote < 0:
        rank = min(rank, 10.0)
    elif vote > 0:
        rank = max(rank, 80.0)
    return round(max(0.0, min(100.0, rank)), 1)


def rank_for(
    job,
    preferences: dict,
    feedback_data: dict,
    signals: dict | None = None,
    digest: str | None = None,
    calibration: tuple[float, float] = (1.0, 0.0),
    taste_score: float | None = None,
) -> tuple[float, dict]:
    rule, notes = rule_score(job, preferences, feedback_data)
    fit = calibrated_fit(signals, calibration)
    llm = None
    if digest is not None and verdict_is_fresh(job, digest):
        llm = float(job["match_score"])
    rank = blend(rule, fit, llm, taste_score, _vote(job))
    return rank, {
        "rule": round(rule, 1),
        "fit": fit,
        "llm": llm,
        "taste": taste_score,
        "notes": notes,
    }


def after_vote(job_id: int) -> None:
    """Re-rank everything at once without the slow parts: those were stored by the last full pass."""
    try:
        parts = {int(key): value for key, value in json.loads(store.meta_get(PARTS_KEY) or "{}").items()}
    except ValueError:
        parts = {}
    if job_id not in parts:
        rerank_all()
        return
    digest = _current_digest()
    jobs = [row for row in store.light_job_states() if row["id"] in parts]
    tastes = taste.scores(jobs, profile.read_preferences(), lambda job: verdict_is_fresh(job, digest))
    weights = read_weights()
    scores = {}
    for job in jobs:
        rule, fit, llm = parts[job["id"]]
        score = blend(rule, fit, llm, tastes.get(job["id"]), _vote(job), weights)
        if score != job["rank_score"]:
            scores[job["id"]] = score
    store.set_rank_scores(scores)


def after_verdict(job_id: int, score: float) -> None:
    """A fresh verdict for one job joins the stored parts, then everything re-blends cheaply."""
    try:
        parts = json.loads(store.meta_get(PARTS_KEY) or "{}")
    except ValueError:
        parts = {}
    if str(job_id) in parts:
        parts[str(job_id)][2] = score
        store.meta_set(PARTS_KEY, json.dumps(parts, separators=(",", ":")))
    after_vote(job_id)


def rerank_all(
    preferences: dict | None = None,
    feedback_data: dict | None = None,
    digest: str | None = None,
) -> int:
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return 0
    prefs = preferences if preferences is not None else profile.read_preferences()
    data = feedback_data if feedback_data is not None else feedback.load()
    digest = digest or _current_digest(prefs)
    with geo.remembered_home():
        refresh_travel()  # Spare the next list view this work.
        jobs = store.list_jobs_for_matching()
        calibration = fit_calibration(jobs, digest)
        store.meta_set(
            "fit_calibration",
            json.dumps({"slope": calibration[0], "intercept": calibration[1]}),
        )
        tastes = taste.scores(jobs, prefs, lambda job: verdict_is_fresh(job, digest))
        scores: dict[int, float] = {}
        parts: dict[int, list] = {}
        for job in jobs:
            rank, detail = rank_for(
                job, prefs, data, signals_of(job), digest, calibration, tastes.get(job["id"])
            )
            scores[job["id"]] = rank
            parts[job["id"]] = [detail["rule"], detail["fit"], detail["llm"]]
        store.set_rank_scores(scores)
        store.meta_set(PARTS_KEY, json.dumps(parts, separators=(",", ":")))
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
    for job in store.list_jobs_for_matching():
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
