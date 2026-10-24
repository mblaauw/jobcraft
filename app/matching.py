"""Rules scoring plus LLM verdicts for job matching."""

from __future__ import annotations

import hashlib
import json
import os

from . import feedback, geo, llm, profile, ranking, standards, store, systemone, tenancy
from .scoring.digests import job_match_digest, profile_hash, public_profile_digest
from .scoring.rules import (
    _row_get,
    rule_score,  # noqa: F401 - public compatibility export
)
from .scoring.rules import hard_filter_keywords as hard_filter_keywords
from .util import parse_json_object

# Bump when the matcher prompt or its instructions change: cached verdicts must
# not survive a prompt change even when preferences and vacancy text are equal.
VERDICT_PROMPT_VERSION = "verdict-2026-10-24-public-preferences-v2"
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
        f"Beoordeel deze vacature voor {tenancy.display_name()}.\n\n"
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
    """Verdict cache key: preferences, public profile and prompt version.

    Feedback is deliberately absent: votes rerank deterministically and never
    invalidate an expensive verdict.
    """
    prefs = preferences if preferences is not None else profile.read_preferences()
    raw = f"{profile_hash(prefs)}:{public_profile_digest(profile_excerpt)}:{VERDICT_PROMPT_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


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
        "errors": [],
    }

    # Stage 1 — System One gate: typed signals, cheap and cached per job.
    if systemone.enabled():
        excerpt = profile.read_public_excerpt(4000)
        questions = systemone.questions_for(preferences)
        batch = int(os.environ.get("SYSTEMONE_BATCH", "40"))
        pending = [
            job
            for job in store.list_jobs_for_matching(2000)
            if job["signals_key"]
            != systemone.signals_key(job, excerpt, preferences=preferences, questions=questions)
            and not ranking.excluded_from_matching(job)
        ]
        consecutive_failures = 0
        for job in pending[:batch]:
            try:
                key = systemone.signals_key(
                    job, excerpt, preferences=preferences, questions=questions
                )
                data, usage, _latency = systemone.decide(
                    systemone.compose_state(job, excerpt, preferences=preferences),
                    questions=questions,
                )
                store.record_usage(
                    None,
                    "systemone",
                    int(usage.get("input_tokens") or 0),
                    int(usage.get("output_tokens") or 0),
                    0.0,
                    agent="jev",
                )
                signals = systemone.signals_from_answers(data.get("answers") or {}, questions)
                rank, _ = ranking.rank_for(job, preferences, feedback_data, signals, digest_base)
                store.update_job_signals(
                    job["id"],
                    rank,
                    json.dumps(signals, ensure_ascii=False),
                    key,
                    float(signals["confidence"]),
                )
                summary["signals"] += 1
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001 - one bad job must not stop the round
                summary["errors"].append(f"job {job['id']} systemone: {type(exc).__name__}")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    summary["errors"].append("systemone gestopt na 3 opeenvolgende fouten")
                    break

    # Stage 2 — free deterministic rerank: votes and fresh signals apply instantly.
    ranking.rerank_all(preferences, feedback_data, digest_base)

    # Stage 3 — LLM only where it adds value: promising and uncertain jobs.
    candidates = ranking.escalation_candidates(digest_base, limit, preferences, feedback_data)
    summary["escalated"] = len(candidates)
    for job in candidates:
        digest = job_match_digest(digest_base, job)
        base, notes = rule_score(job, preferences, feedback_data)
        try:
            text, usage = mgr.generate(
                verdict_prompt(job, preferences, profile_md), agent="matcher"
            )
            # A completed paid call must be accounted for even when its answer
            # cannot be parsed. Record before validating the provider text.
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
            summary["matched"] += 1
        except Exception as exc:  # noqa: BLE001 - retry next round via the queue
            summary["failed"] += 1
            summary["errors"].append(f"job {job['id']}: {type(exc).__name__}")

    # Blend the new LLM verdicts into the deterministic rank, then report the queue.
    ranking.rerank_all(preferences, feedback_data, digest_base)
    summary["remaining"] = ranking.queue_depth(digest_base)
    return summary
