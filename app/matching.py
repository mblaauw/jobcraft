"""Rules scoring plus LLM verdicts for job matching."""

from __future__ import annotations

import json
import os

from . import feedback, geo, llm, profile, ranking, standards, store, systemone
from .llm_client import LLMSchemaError
from .scoring import digests

# Bump when the matcher prompt or its instructions change: cached verdicts must
# not survive a prompt change even when preferences and vacancy text are equal.
from .scoring.digests import VERDICT_PROMPT_VERSION as VERDICT_PROMPT_VERSION
from .scoring.digests import job_match_digest
from .scoring.rules import (
    _row_get,
    rule_score,
)
from .scoring.rules import hard_filter_keywords as hard_filter_keywords
from .util import now_local, parse_json_object

PROFILE_PROMPT_LIMIT = 4000


def _parse_verdict(text: str) -> dict:
    data = parse_json_object(text)
    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("matcher score missing or not numeric")
    return {
        "score": max(0, min(100, float(score))),
        "why": str(data.get("why") or "").strip(),
        "concerns": [str(x) for x in (data.get("concerns") or [])][:8],
        "questions_for_employer": [str(x) for x in (data.get("questions_for_employer") or [])][:6],
    }


def verdict_prompt(job, preferences: dict, profile_md: str) -> str:
    travel = geo.assess(
        _row_get(job, "location"),
        _row_get(job, "lat"),
        _row_get(job, "lon"),
        description=_row_get(job, "description"),
        title=_row_get(job, "title"),
    )
    vacancy_standards = standards.standards_block(
        ("vacature-analyse",), heading="## Vacature-analyse (verplichte velden)"
    )
    standards_part = f"{vacancy_standards}\n\n" if vacancy_standards else ""
    return (
        "Beoordeel deze vacature voor deze gebruiker.\n\n"
        "## Voorkeuren (JSON)\n"
        f"{json.dumps(profile.public_preferences(preferences), ensure_ascii=False)}\n\n"
        "## Profiel (samenvatting)\n"
        f"{profile_md[:PROFILE_PROMPT_LIMIT]}\n\n"
        "## Reistijd vanaf de woonplaats in de voorkeuren (schatting)\n"
        f"{travel['label']} · afstand ~{travel['distance_km']} km\n\n"
        f"{standards_part}"
        "## Vacature\n"
        f"Titel: {job['title']}\n"
        f"Organisatie: {job['company'] or 'onbekend'}\n"
        f"Bron: {job['source_id']}\n"
        f"URL: {job['url']}\n\n"
        f"{job['description'][:8000]}\n\n"
        "Antwoord met alleen de gevraagde JSON."
    )


def current_digest(preferences: dict | None = None, profile_excerpt: str | None = None) -> str:
    """Verdict cache key: what the prompt reads from the preferences, the public profile and
    the prompt version.

    Feedback is deliberately absent: votes rerank deterministically and never
    invalidate an expensive verdict.
    """
    return digests.current_digest(preferences, profile_excerpt, prompt_version=VERDICT_PROMPT_VERSION)


FAILED_KEY = "verdict_failed"


def _failed_today() -> set[int]:
    """Vacancies whose full assessment failed today; they get another try tomorrow."""
    try:
        stored = json.loads(store.meta_get(FAILED_KEY) or "{}")
    except ValueError:
        return set()
    return set(stored.get("ids") or []) if stored.get("day") == now_local().date().isoformat() else set()


def _ask_system_one(job, state: str, questions: dict) -> dict:
    data, usage, _latency = systemone.decide(state, questions=questions)
    store.record_usage(
        None,
        "systemone",
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        float(usage.get("cost_usd") or 0.0),
        agent="jev",
    )
    return data.get("answers") or {}


def _systemone_calls_left() -> int:
    """Jev calls are logged at $0, so the dollar budget cannot limit them; the daily count does."""
    daily_limit = int(os.environ.get("SYSTEMONE_DAILY_LIMIT", "80"))
    return max(0, daily_limit - store.daily_usage_calls("systemone"))


def _system_one_round(preferences, feedback_data, digest_base, summary) -> str:
    """Spend one batch of the daily calls on the most promising jobs first.

    Facts about a vacancy are asked once for all accounts and survive profile edits; the
    personal fit is only asked for the jobs ranked highest for this account. Returns
    "covered" when every job above the fit line has its answer, "spent" when the batch
    ran out before that, and "failed" when Jev kept failing.
    """
    excerpt = profile.read_public_excerpt(4000)
    questions = systemone.questions_for(preferences)
    batch = int(os.environ.get("SYSTEMONE_BATCH", "40"))
    calls_left = min(batch, _systemone_calls_left())
    fit_top = int(os.environ.get("SYSTEMONE_FIT_TOP", "200"))
    top_waiting = False

    ranking.rerank_all(preferences, feedback_data, digest_base, route=False)
    jobs = sorted(
        store.list_jobs_for_matching(),
        key=lambda job: job["rank_score"] if job["rank_score"] is not None else -1,
        reverse=True,
    )
    shared = store.load_job_facts()
    consecutive_failures = 0
    position = 0
    for job in jobs:
        if ranking.excluded_from_matching(job):
            continue
        position += 1
        key = systemone.signals_key(job, excerpt, preferences=preferences, questions=questions)
        fact_key = systemone.facts_key(job)
        known = shared.get(job["id"])
        facts = known["answers"] if known and known.get("key") == fact_key else None
        if job["signals_key"] == key:
            if facts is None:  # A combined answer from before facts were shared.
                old = systemone.facts_from_signals(ranking.signals_of(job))
                if old:
                    store.save_job_facts(job["id"], fact_key, old)
            continue
        wants_fit = position <= fit_top
        if facts is not None and not wants_fit and job["signals_key"] == f"facts:{fact_key}":
            continue
        needed = (facts is None) + (wants_fit and bool(calls_left))
        if wants_fit and calls_left <= 0:
            top_waiting = True  # the batch ran out above the fit line: another batch is worth it
        if facts is None and calls_left <= 0:
            continue
        if wants_fit and calls_left <= 0 and ranking.signals_of(job):
            # Out of calls: the last answer, fit included, is better than facts alone.
            continue
        try:
            if facts is None:
                facts = _ask_system_one(
                    job, systemone.compose_facts_state(job), systemone.facts_questions()
                )
                calls_left -= 1
                store.save_job_facts(job["id"], fact_key, facts)
            answers = dict(facts)
            new_key = f"facts:{fact_key}"
            if wants_fit and calls_left > 0:
                answers.update(
                    _ask_system_one(
                        job,
                        systemone.compose_state(job, excerpt, preferences=preferences),
                        systemone.personal_questions(questions),
                    )
                )
                calls_left -= 1
                new_key = key
            signals = systemone.signals_from_answers(answers, questions)
            rank, _ = ranking.rank_for(job, preferences, feedback_data, signals, digest_base)
            store.update_job_signals(
                job["id"],
                rank,
                json.dumps(signals, ensure_ascii=False),
                new_key,
                float(signals["confidence"]),
            )
            if needed:
                summary["signals"] += 1
            consecutive_failures = 0
        except Exception as exc:
            summary["errors"].append(f"job {job['id']} systemone: {type(exc).__name__}")
            consecutive_failures += 1
            if consecutive_failures >= 3:
                summary["errors"].append("systemone gestopt na 3 opeenvolgende fouten")
                return "failed"
    return "spent" if top_waiting else "covered"


def signal_one(
    job,
    preferences: dict | None = None,
    feedback_data: dict | None = None,
    digest_base: str | None = None,
) -> dict | None:
    """Jev for one vacancy, now: shared facts when known, then the personal fit.

    None when Jev is off or the daily calls are spent; the caller then judges as before.
    """
    if not systemone.enabled():
        return None
    calls_left = _systemone_calls_left()
    preferences = preferences if preferences is not None else profile.read_preferences()
    feedback_data = feedback_data if feedback_data is not None else feedback.load()
    digest_base = digest_base or current_digest(preferences)
    excerpt = profile.read_public_excerpt(4000)
    questions = systemone.questions_for(preferences)
    fact_key = systemone.facts_key(job)
    known = store.get_job_facts(job["id"])
    facts = known["answers"] if known and known.get("key") == fact_key else None
    if (facts is None) + 1 > calls_left:
        return None
    if facts is None:
        facts = _ask_system_one(job, systemone.compose_facts_state(job), systemone.facts_questions())
        store.save_job_facts(job["id"], fact_key, facts)
    answers = dict(facts)
    answers.update(
        _ask_system_one(
            job,
            systemone.compose_state(job, excerpt, preferences=preferences),
            systemone.personal_questions(questions),
        )
    )
    signals = systemone.signals_from_answers(answers, questions)
    rank, _ = ranking.rank_for(job, preferences, feedback_data, signals, digest_base)
    store.update_job_signals(
        job["id"],
        rank,
        json.dumps(signals, ensure_ascii=False),
        systemone.signals_key(job, excerpt, preferences=preferences, questions=questions),
        float(signals["confidence"]),
    )
    return signals


def judge(mgr, job, preferences, profile_md, feedback_data, digest_base, summary=None) -> float:
    """One full LLM verdict, stored on the job; returns its score."""
    digest = job_match_digest(digest_base, job)
    base, notes = rule_score(job, preferences, feedback_data)
    text, usage = mgr.generate(verdict_prompt(job, preferences, profile_md), agent="matcher")
    # A completed paid call must be accounted for even when its answer
    # cannot be parsed. Record before validating the provider text.
    if summary is not None:
        summary["cost_usd"] += float(usage.get("cost_usd") or 0.0)
    store.record_usage(
        None,
        "matching",
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        float(usage.get("cost_usd") or 0.0),
        agent="matcher",
    )
    verdict = _parse_verdict(text)
    reason = json.dumps(
        {
            "why": verdict["why"],
            "concerns": verdict["concerns"],
            "questions_for_employer": verdict["questions_for_employer"],
            "rule_score": round(base, 1),
            "rule_notes": notes,
            "signals": ranking.signals_of(job),
        },
        ensure_ascii=False,
    )
    store.update_job_match(job["id"], float(verdict["score"]), reason, digest)
    return float(verdict["score"])


def match_jobs(mgr: llm.ServerManager, limit: int | None = None) -> dict:
    limit = limit or int(os.environ.get("MATCH_BATCH", "12"))
    preferences = profile.read_preferences()
    profile_md = profile.read_public_excerpt(PROFILE_PROMPT_LIMIT * 2)
    feedback_data = feedback.load()
    digest_base = current_digest(preferences, profile_md)
    summary = {
        "matched": 0,
        "failed": 0,
        "cost_usd": 0.0,
        "signals": 0,
        "escalated": 0,
        "sampled": 0,
        "errors": [],
    }

    # Stage 1 — System One gate: shared job facts once, personal fit only where it counts.
    # Batches keep going while the best-ranked vacancies still wait and the day's calls
    # allow, so a scan that brings in thousands does not leave the top of the list unassessed.
    if systemone.enabled():
        for _ in range(max(1, int(os.environ.get("SYSTEMONE_ROUNDS", "20")))):
            before = summary["signals"]
            with geo.remembered_home():  # read once per batch; it was read again for every vacancy
                outcome = _system_one_round(preferences, feedback_data, digest_base, summary)
            if outcome != "spent" or summary["signals"] == before or _systemone_calls_left() <= 0:
                break  # covered, failed, a batch that answered nothing, or the day's calls are spent

    # Stage 2 — free deterministic rerank: votes and fresh signals apply instantly.
    # Stage 3 and the closing pass route on these ranks themselves, so this one does not.
    ranking.rerank_all(preferences, feedback_data, digest_base, route=False)

    # Stage 3 — LLM only where the route says so: promising and uncertain jobs, plus a
    # small sample of sure fits inside the same batch, so the calibration stays honest.
    # Without a configured model the verdicts simply wait; nothing has failed.
    available = mgr is not None and getattr(mgr, "client", True) is not None
    candidates, sample = [], []
    failed_today = _failed_today()
    if available:
        decided = ranking.routed(digest_base, preferences, feedback_data)
        sample = [
            job
            for job in ranking.calibration_sample(digest_base, preferences, feedback_data, decided)
            if job["id"] not in failed_today
        ][:limit]
        # Not the ones that already failed today: the best-ranked vacancy whose answer cannot
        # be read would otherwise be bought again at the top of every round.
        candidates = [
            job
            for job in ranking.escalation_candidates(
                digest_base, limit + len(failed_today), preferences, feedback_data, decided
            )
            if job["id"] not in failed_today
        ][: max(0, limit - len(sample))]
    summary["escalated"] = len(candidates)
    summary["sampled"] = len(sample)
    for job in [*candidates, *sample]:
        try:
            judge(mgr, job, preferences, profile_md, feedback_data, digest_base, summary)
            summary["matched"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append(f"job {job['id']}: {type(exc).__name__}")
            if isinstance(exc, ValueError | KeyError | TypeError | LLMSchemaError):
                # An answer that cannot be read is this vacancy's problem. An expired key, a
                # rate limit or a timeout is not: those vacancies must be tried again at once.
                failed_today.add(job["id"])
                store.meta_set(FAILED_KEY, json.dumps({"day": now_local().date().isoformat(), "ids": sorted(failed_today)[-200:]}))

    # Blend the new LLM verdicts into the deterministic rank, then report where things stand.
    ranking.rerank_all(preferences, feedback_data, digest_base)
    summary["remaining"] = ranking.queue_depth(digest_base)
    summary["routes"] = ranking.route_counts(digest_base)
    return summary
