"""System One decision gate (Runware → Jev).

Jev answers *typed* questions (noul / choice / score) with probabilities and a
calibrated confidence, in one parallel pass. It does not generate prose. We use it
as the cheap first-stage judge for every vacancy; only low-confidence cases are
escalated to the LLM matcher (System 2).

Contract (verified live 2026-10-18):
    POST https://api.runware.ai/v1/decisions
    Authorization: Bearer $RUNWARE_API_KEY
    {"model": "typesafe:jev@1", "state": "...", "questions": {...}}

Schema gotchas:
    - score.criteria is an ORDERED ARRAY of 2–10 level descriptions
    - choice.criteria is a map option -> description (or null)
    - noul.criteria is optional {"true": "...", "false": "..."}
    - response: answers.<id> = {type, choice|noul|score, probabilities, confidence}
      (noul has no confidence; use max(p, 1-p))
"""

from __future__ import annotations

import copy
import hashlib
import os
import time

import httpx

from . import profile, tenancy

DEFAULT_URL = "https://api.runware.ai/v1/decisions"
DEFAULT_MODEL = "typesafe:jev@1"
QUESTION_VERSION = "s1-2026-10-19-2"
DESCRIPTION_LIMIT = 3000
PROFILE_LIMIT = 600

CATEGORY_CRITERIA = {
    "groen_buiten": "groen, parken, tuinen, planten, buitenwerk, natuur",
    "facilitair": "locatie, receptie, beheer, huismeester, facilitair, logistiek",
    "cultuur_community": "cultuur, community, gastheer, activiteiten, welzijn",
    "zorg_welzijn": "zorg, ondersteuning, begeleiding van mensen",
    "sales_telefoon": "verkoop, bellen, callcenter, acquisitie, targets",
    "kantoor_beleid": "beleid, administratie, management, kantoorwerk",
    "overig": "past niet in de andere categorieën",
}

QUESTIONS: dict[str, dict] = {
    "fit": {
        "type": "score",
        "instructions": (
            "Hoe goed past deze vacature bij de gebruiker en diens eigen voorkeuren, "
            "uren, reisafstand en werkomstandigheden? Baseer je alleen op het "
            "meegeleverde profiel en de vacaturetekst."
        ),
        "criteria": ["slecht", "matig", "redelijk", "goed", "uitstekend"],
    },
    "category": {
        "type": "choice",
        "instructions": "Wat voor soort functie is dit?",
        "criteria": CATEGORY_CRITERIA,
    },
    "phone_sales_main": {
        "type": "noul",
        "instructions": "Is telefonie, sales of acquisitie de hoofdmoot van deze functie?",
        "criteria": {
            "true": "bellen of verkopen is het grootste deel van het werk",
            "false": "nauwelijks telefoon of verkoop",
        },
    },
    "evening_weekend": {
        "type": "noul",
        "instructions": "Vereist de functie structureel avond- of weekendwerk?",
    },
    "physically_heavy": {
        "type": "noul",
        "instructions": "Is dit zwaar fysiek werk (veel tillen, machines, bouw of sloop)?",
    },
    "outdoor_mobile": {
        "type": "noul",
        "instructions": "Werkt deze persoon het grootste deel van de dag buiten of op wisselende locaties?",
    },
    "company_car": {
        "type": "noul",
        "instructions": "Biedt de werkgever een bedrijfsauto, of is rijden langs locaties onderdeel van het werk?",
    },
    "guidance": {
        "type": "noul",
        "instructions": "Is er een duidelijke inwerkperiode of begeleiding voor iemand die nieuw is in dit werk?",
    },
    "management_policy": {
        "type": "noul",
        "instructions": "Is dit een management-, beleids- of senior kantoorfunctie?",
    },
    "day_hours": {
        "type": "noul",
        "instructions": "Is dit overwegend dagwerk (geen ploegendienst of nachtwerk)?",
    },
    "flexible_remote": {
        "type": "noul",
        "instructions": (
            "Is de functie combineerbaar met periodiek op afstand werken "
            "(parttime, flexibel of seizoenswerk)?"
        ),
    },
}


class SystemOneError(RuntimeError):
    pass


def api_key() -> str:
    return os.environ.get("RUNWARE_API_KEY", "").strip()


def enabled() -> bool:
    if os.environ.get("SYSTEMONE_ENABLED", "1") in {"0", "false", "no"}:
        return False
    return bool(api_key())


def confidence_threshold() -> float:
    try:
        return float(os.environ.get("SYSTEMONE_CONFIDENCE", "0.75"))
    except ValueError:
        return 0.75


def model_id() -> str:
    return os.environ.get("SYSTEMONE_MODEL", DEFAULT_MODEL)


def endpoint() -> str:
    return os.environ.get("SYSTEMONE_URL", DEFAULT_URL)


def compose_state(job, profile_excerpt: str = "") -> str:
    parts = [
        "Vacature",
        f"Titel: {job['title'] or ''}",
        f"Organisatie: {job['company'] or 'onbekend'}",
        f"Locatie: {job['location'] or 'onbekend'}",
        f"Uren: {job['hours'] or 'onbekend'}",
        "",
        (job["description"] or "")[:DESCRIPTION_LIMIT],
    ]
    if profile_excerpt:
        parts += ["", "Profiel (publiek, ingekort):", profile_excerpt[:PROFILE_LIMIT]]
    if tenancy.user_id() != tenancy.LEGACY_ID:
        import json

        parts += [
            "Voorkeuren van deze gebruiker:",
            json.dumps(profile.read_preferences(), ensure_ascii=False),
        ]
    return "\n".join(parts)


def decide(
    state: str, questions: dict | None = None, model: str | None = None
) -> tuple[dict, dict, int]:
    key = api_key()
    if not key:
        raise SystemOneError("RUNWARE_API_KEY ontbreekt")
    effective_questions = questions or QUESTIONS
    if questions is None and tenancy.user_id() != tenancy.LEGACY_ID:
        effective_questions = copy.deepcopy(QUESTIONS)
        effective_questions["fit"]["instructions"] = (
            "Hoe goed past deze vacature bij de meegeleverde gebruiker en diens eigen voorkeuren? Neem geen voorkeuren aan die niet in het profiel staan."
        )
    payload = {
        "model": model or model_id(),
        "state": state,
        "questions": effective_questions,
    }
    start = time.perf_counter()
    try:
        response = httpx.post(
            endpoint(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=float(os.environ.get("SYSTEMONE_TIMEOUT", "45")),
        )
    except httpx.HTTPError as exc:
        raise SystemOneError(f"transport: {type(exc).__name__}") from exc
    latency_ms = int((time.perf_counter() - start) * 1000)
    if response.status_code >= 400:
        raise SystemOneError(f"HTTP {response.status_code}: {response.text[:200]}")
    data = response.json()
    return data, data.get("usage") or {}, latency_ms


def _fit_scale(questions: dict | None = None) -> int:
    criteria = (questions or QUESTIONS)["fit"]["criteria"]
    return max(1, len(criteria) - 1)


def signals_from_answers(answers: dict, questions: dict | None = None) -> dict:
    fit = answers.get("fit") or {}
    category = answers.get("category") or {}
    confidences: list[float] = []
    scale = _fit_scale(questions)
    fit_value = None
    if fit:
        try:
            fit_value = round(float(fit.get("score", 0)) / scale * 100, 1)
        except (TypeError, ValueError):
            fit_value = None
        if "confidence" in fit:
            confidences.append(float(fit["confidence"]))
    if "confidence" in category:
        confidences.append(float(category["confidence"]))

    noul: dict[str, float] = {}
    for key, answer in answers.items():
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            continue
        try:
            probability = float(answer.get("noul", 0.5))
        except (TypeError, ValueError):
            probability = 0.5
        noul[key] = round(probability, 3)
        confidences.append(max(probability, 1 - probability))

    confidence = round(min(confidences), 3) if confidences else 0.0
    return {
        "version": QUESTION_VERSION,
        "fit": fit_value,
        "category": category.get("choice"),
        "category_probabilities": category.get("probabilities"),
        "confidence": confidence,
        "noul": noul,
        "escalate": confidence < confidence_threshold(),
    }


def signals_key(job, profile_excerpt: str) -> str:
    raw = f"{QUESTION_VERSION}:{job['content_hash'] or ''}:{hashlib.sha256(profile_excerpt.encode()).hexdigest()[:16]}"
    if tenancy.user_id() != tenancy.LEGACY_ID:
        # New-account decisions also receive structured preferences. Keep the
        # existing legacy key intact, but never reuse new-user signals after a
        # preference change. Feedback is deliberately excluded.
        import json

        stable = {
            key: value for key, value in profile.read_preferences().items() if key != "updated_at"
        }
        raw += json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]
