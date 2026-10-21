"""Rules scoring plus LLM verdicts for job matching."""

from __future__ import annotations

import hashlib
import json
import os
import re

from . import feedback, geo, llm, profile, ranking, standards, store, systemone, tenancy
from .util import parse_json_object

# Bump when the matcher prompt or its instructions change: cached verdicts must
# not survive a prompt change even when preferences and vacancy text are equal.
VERDICT_PROMPT_VERSION = "verdict-2026-10-19-1"
PROFILE_PROMPT_LIMIT = 4000


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower())


def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def rule_score(
    job, preferences: dict, feedback_data: dict | None = None
) -> tuple[float, list[str]]:
    """Fast deterministic pre-score in 0..100 with short reason notes."""
    text = _row_get(job, "search_text") or _norm(
        " ".join([job["title"] or "", job["company"] or "", job["description"] or ""])
    )
    score = 40.0
    notes: list[str] = []

    for track in preferences.get("tracks", []):
        keywords = [kw for kw in track.get("keywords", []) if kw]
        hits = sum(1 for kw in keywords if kw.lower() in text)
        if hits:
            score += min(5, hits) * (2.0 * float(track.get("weight", 1.0)))
            notes.append(f"track {track.get('id')}: {hits} keyword(s)")

    for item in preferences.get("likes", []):
        tag = (item.get("tag") or "").lower()
        if tag and tag in text:
            score += min(3.0, float(item.get("weight", 1.0))) * 1.5

    for item in preferences.get("dislikes", []):
        tag = (item.get("tag") or "").lower()
        if tag and tag in text:
            score += min(0.0, float(item.get("weight", -1.0))) * 2.0
            notes.append(f"dislike: {tag}")

    for keyword in preferences.get("keywords", {}).get("boost", []):
        if keyword and keyword.lower() in text:
            score += 1.0
    for keyword in preferences.get("keywords", {}).get("veto", []):
        if keyword and keyword.lower() in text:
            score -= 12.0
            notes.append(f"veto: {keyword}")

    home = (preferences.get("home") or {}).get("label") or ""
    home_label = _norm(home)
    if home_label and home_label != "nog niet ingesteld" and home_label in text:
        score += 6.0
        notes.append(f"{home} vermeld")
    hours = preferences.get("hard_filters", {})
    try:
        hours_min = float(hours.get("hours_min"))
        hours_max = float(hours.get("hours_max"))
    except (TypeError, ValueError):
        hours_min = hours_max = 0.0
    match = re.search(r"\b(\d{1,2})\s*(?:uur|hours?)\b", text)
    if match and hours_min and hours_max:
        offered = float(match.group(1))
        if hours_min <= offered <= hours_max:
            score += 4.0
            notes.append(f"{int(hours_min)}-{int(hours_max)} uur vermeld")
        elif offered > hours_max:
            score -= 3.0
    filters = preferences.get("hard_filters", {})
    noul = (ranking.signals_of(job) or {}).get("noul") or {}

    def probability(key):
        try:
            return float(noul[key])
        except (KeyError, TypeError, ValueError):
            return None

    phone = probability("phone_sales_main")
    if filters.get("no_phone_main"):
        if phone is not None and phone >= 0.75:
            score -= 15.0
        elif phone is None and "callcenter" in text:
            score -= 10.0
    if filters.get("no_structural_evenings") and (probability("evening_weekend") or 0) >= 0.75:
        score -= 12.0
    if filters.get("no_heavy_physical") and (probability("physically_heavy") or 0) >= 0.75:
        score -= 12.0
    if filters.get("day_work_only"):
        day = probability("day_hours")
        if day is not None and day <= 0.25:
            score -= 10.0
    if filters.get("wants_guidance") and (probability("guidance") or 0) >= 0.75:
        score += 4.0

    travel = ranking.travel_of(job)
    if travel.get("in_range") is False and not travel.get("auto"):
        score -= 25.0
        notes.append(f"te ver: {travel['label']}")
    elif travel.get("in_range") is True:
        score += 4.0
        notes.append("binnen reisbereik (schatting)")
    elif travel.get("auto"):
        notes.append("mobiel werk met auto")

    fb_delta, fb_notes = feedback.rule_adjust(
        text, _row_get(job, "company"), _row_get(job, "source_id"), feedback_data
    )
    score += fb_delta
    notes.extend(fb_notes)

    return max(0.0, min(100.0, score)), notes


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
        f"{json.dumps(preferences, ensure_ascii=False)}\n\n"
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


def profile_hash(preferences: dict) -> str:
    # updated_at is volatile bookkeeping and must not invalidate cached verdicts.
    stable = {key: value for key, value in preferences.items() if key != "updated_at"}
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def public_profile_digest(excerpt: str | None = None) -> str:
    text = profile.read_public_excerpt(PROFILE_PROMPT_LIMIT * 2) if excerpt is None else excerpt
    return hashlib.sha256(text[:PROFILE_PROMPT_LIMIT].encode()).hexdigest()[:16]


def current_digest(preferences: dict | None = None, profile_excerpt: str | None = None) -> str:
    """Verdict cache key: preferences, public profile and prompt version.

    Feedback is deliberately absent: votes rerank deterministically and never
    invalidate an expensive verdict.
    """
    prefs = preferences if preferences is not None else profile.read_preferences()
    raw = f"{profile_hash(prefs)}:{public_profile_digest(profile_excerpt)}:{VERDICT_PROMPT_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def job_match_digest(profile_digest: str, job) -> str:
    """Fingerprint of everything that feeds one LLM verdict (no feedback)."""
    raw = f"{profile_digest}:{job['content_hash'] or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


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
        batch = int(os.environ.get("SYSTEMONE_BATCH", "40"))
        pending = [
            job
            for job in store.list_jobs_for_matching(2000)
            if job["signals_key"] != systemone.signals_key(job, excerpt)
            and not ranking.excluded_from_matching(job)
        ]
        consecutive_failures = 0
        for job in pending[:batch]:
            try:
                data, usage, _latency = systemone.decide(systemone.compose_state(job, excerpt))
                signals = systemone.signals_from_answers(data.get("answers") or {})
                rank, _ = ranking.rank_for(job, preferences, feedback_data, signals, digest_base)
                store.update_job_signals(
                    job["id"],
                    rank,
                    json.dumps(signals, ensure_ascii=False),
                    systemone.signals_key(job, excerpt),
                    float(signals["confidence"]),
                )
                store.record_usage(
                    None,
                    "systemone",
                    int(usage.get("input_tokens") or 0),
                    int(usage.get("output_tokens") or 0),
                    0.0,
                    agent="jev",
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
