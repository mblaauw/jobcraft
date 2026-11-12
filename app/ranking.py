"""Deterministic ranking on top of the rule score, System One signals and votes.

The important property: this module can re-rank every job in milliseconds, so a
vote never invalidates an expensive verdict. Feedback only moves the rule score;
the Jev/LLM verdicts stay cached.
"""

from __future__ import annotations

import json

from . import feedback, memory, profile, store, systemone, tasks, taste, tenancy
from . import geo as geo
from .scoring.digests import current_digest as _current_digest
from .scoring.digests import job_match_digest
from .scoring.route import HANDLED_STATUSES, OUTCOMES, Route, Thresholds, read_thresholds
from .scoring.route import route as _route
from .scoring.rules import rule_score
from .scoring.signals import excluded_from_matching as excluded_from_matching
from .scoring.signals import refresh_travel as refresh_travel
from .scoring.signals import signals_of
from .scoring.signals import travel_of as travel_of
from .scoring.weights import read_weights


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


def after_vote(job_id: int, *, full_pass_here: bool = False) -> None:
    """Re-rank everything at once without the slow parts: those were stored by the last full pass.

    A vacancy the last full pass did not see (new since then, or older than the newest
    MATCHING_LIMIT) needs that pass again. A request queues it for the worker: at 25,000
    vacancies it takes half a minute and more memory than the web process has. Worker
    tasks that route on the fresh rank right after pass ``full_pass_here``."""
    try:
        parts = {int(key): value for key, value in json.loads(store.meta_get(PARTS_KEY) or "{}").items()}
    except ValueError:
        parts = {}
    if job_id not in parts:
        if full_pass_here:
            rerank_all()
            return
        owner = tenancy.user_id()
        tasks.enqueue("rerank", owner=owner, dedupe_key=f"rerank:{owner}")
        if not parts:
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


def after_signals(job_id: int, signals: dict) -> None:
    """A fresh quick assessment for one job joins the stored parts, then everything re-blends."""
    try:
        parts = json.loads(store.meta_get(PARTS_KEY) or "{}")
        stored = json.loads(store.meta_get("fit_calibration") or "{}")
        calibration = (float(stored.get("slope", 1.0)), float(stored.get("intercept", 0.0)))
    except (ValueError, TypeError):
        parts, calibration = {}, (1.0, 0.0)
    if str(job_id) in parts:
        parts[str(job_id)][1] = calibrated_fit(signals, calibration)
        store.meta_set(PARTS_KEY, json.dumps(parts, separators=(",", ":")))
    after_vote(job_id, full_pass_here=True)  # the worker routes on the new rank next


def after_verdict(job_id: int, score: float) -> None:
    """A fresh verdict for one job joins the stored parts, then everything re-blends cheaply."""
    try:
        parts = json.loads(store.meta_get(PARTS_KEY) or "{}")
    except ValueError:
        parts = {}
    if str(job_id) in parts:
        parts[str(job_id)][2] = score
        store.meta_set(PARTS_KEY, json.dumps(parts, separators=(",", ":")))
    after_vote(job_id, full_pass_here=True)


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
    with memory.catalogue(), geo.remembered_home():
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
        # The routed pass reads every vacancy again with its new rank; without this the
        # two copies of the catalogue are alive together and the pass needs twice the memory.
        del jobs, tastes
        decided = routed(digest, prefs, data)
        _remember_routes(digest, decided)
        del decided  # inside the turn, so the next pass starts with this memory given back
    return len(scores)


TOP_OF_LIST = 200  # the vacancies a person actually scrolls through


def _remember_routes(digest: str, decided: list) -> dict[str, int]:
    """Queue depth and route counts from one routed pass, cached until the digest changes."""
    counts = dict.fromkeys(OUTCOMES, 0)
    for decision, _, _ in decided:
        counts[decision.outcome] += 1
    # How much of the top still waits for Jev: new vacancies land there with an optimistic
    # rank, so an administrator should see when the quick assessment is not keeping up.
    top = _by_rank([(rank, decision) for decision, rank, _ in decided if decision.outcome != "excluded"])[:TOP_OF_LIST]
    store.meta_set(
        "top_wait",
        json.dumps({"waiting": sum(1 for decision in top if decision.outcome == "wait"), "of": len(top)}),
    )
    # Only changes are stored, so the history says when and why a vacancy moved route.
    store.record_routes({job["id"]: f"{decision.outcome}:{decision.code}" for decision, _, job in decided})
    store.meta_set("queue_depth", str(counts["escalate"]))
    store.meta_set("route_counts", json.dumps(counts))
    store.meta_set("queue_depth_digest", digest)
    return counts


def _decide(
    job,
    digest: str,
    preferences: dict | None,
    feedback_data: dict | None,
    thresholds: Thresholds,
    quick: bool,
    wanted: bool = False,
) -> tuple[Route, float | None]:
    """The route for one stored job and the rank it was judged on; ranks a job without one."""
    signals = signals_of(job)
    handled = bool(job["vote"]) or job["status"] in HANDLED_STATUSES
    travel = {} if handled else travel_of(job)
    rank = job["rank_score"]
    if rank is None and signals is not None and signals.get("fit") is not None:
        prefs = preferences if preferences is not None else profile.read_preferences()
        data = feedback_data if feedback_data is not None else feedback.load()
        rank = rank_for(job, prefs, data, signals, digest)[0]
    decision = _route(
        job,
        signals,
        fresh=verdict_is_fresh(job, digest),
        rank=rank,
        too_far=geo.too_far(travel),
        wanted=wanted,
        quick=quick,
        thresholds=thresholds,
    )
    return decision, rank


def route_of(
    job,
    digest: str,
    preferences: dict | None = None,
    feedback_data: dict | None = None,
    *,
    wanted: bool = False,
) -> Route:
    """What happens next with this vacancy for this account, and why."""
    return _decide(
        job, digest, preferences, feedback_data, read_thresholds(), systemone.enabled(), wanted
    )[0]


def routed(
    digest: str, preferences: dict | None = None, feedback_data: dict | None = None
) -> list:
    """(route, rank, job) for every vacancy in matching: one pass that every caller shares."""
    thresholds = read_thresholds()
    quick = systemone.enabled()
    with memory.catalogue():
        return [
            (*_decide(job, digest, preferences, feedback_data, thresholds, quick), job)
            for job in store.list_jobs_for_matching()
        ]


def _by_rank(rows) -> list:
    return [job for _, job in sorted(rows, key=lambda item: item[0] if item[0] is not None else -1.0, reverse=True)]


def escalation_candidates(
    digest: str,
    limit: int,
    preferences: dict | None = None,
    feedback_data: dict | None = None,
    decided: list | None = None,
) -> list:
    """Jobs worth an expensive LLM verdict: the route says escalate, best rank first."""
    decided = decided if decided is not None else routed(digest, preferences, feedback_data)
    rows = [(rank, job) for decision, rank, job in decided if decision.outcome == "escalate"]
    return _by_rank(rows)[:limit]


def calibration_sample(
    digest: str,
    preferences: dict | None = None,
    feedback_data: dict | None = None,
    decided: list | None = None,
    size: int | None = None,
) -> list:
    """A few sure fits judged anyway, so the calibration also sees jobs Jev was sure about.

    Without them every paired verdict would come from the unsure band and the map from
    Jev's scale to the matcher's would bend toward it.
    """
    size = read_thresholds().sample if size is None else size
    if size <= 0:
        return []
    decided = decided if decided is not None else routed(digest, preferences, feedback_data)
    rows = [(rank, job) for decision, rank, job in decided if decision.code == "sure"]
    return _by_rank(rows)[:size]


def routes(
    digest: str, preferences: dict | None = None, feedback_data: dict | None = None
) -> list[Route]:
    """One route per vacancy in matching, for counting and measuring."""
    return [decision for decision, _, _ in routed(digest, preferences, feedback_data)]


def route_counts(digest: str) -> dict[str, int]:
    """How many vacancies sit on each route now; cached by the last rerank for this digest."""
    if store.meta_get("queue_depth_digest") == digest:
        try:
            cached = json.loads(store.meta_get("route_counts") or "")
            if set(cached) == set(OUTCOMES):
                return cached
        except ValueError:
            pass
    return _remember_routes(digest, routed(digest))


def top_wait() -> dict | None:
    """Of the best-ranked vacancies, how many still wait for a quick assessment; None before any rerank."""
    try:
        gap = json.loads(store.meta_get("top_wait") or "")
    except ValueError:
        return None
    return gap if isinstance(gap, dict) and "waiting" in gap and "of" in gap else None


def queue_depth(digest: str, *, compute: bool = True) -> int | None:
    """How many vacancies still wait for a full assessment. Counting it fresh is a routed
    pass over the whole catalogue: some 12 s and hundreds of MB at 25,000 vacancies. A page
    passes ``compute=False``: it gets the last count (None if there is none) and the worker
    is asked for a fresh one, which a full re-rank stores."""
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return 0
    cached = store.meta_get("queue_depth")
    if store.meta_get("queue_depth_digest") == digest and cached is not None:
        return int(cached)
    if not compute:
        owner = tenancy.user_id()
        tasks.enqueue("rerank", owner=owner, dedupe_key=f"rerank:{owner}")
        return int(cached) if cached is not None else None
    return _remember_routes(digest, routed(digest))["escalate"]
