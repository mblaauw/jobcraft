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
import json
import os
import time
from functools import lru_cache
from pathlib import Path

import httpx
import yaml

from . import profile

DEFAULT_URL = "https://api.runware.ai/v1/decisions"
DEFAULT_MODEL = "typesafe:jev@1"
QUESTION_VERSION = "s1-account-categories-v1"
DESCRIPTION_LIMIT = 3000
PROFILE_LIMIT = 600


@lru_cache(maxsize=1)
def default_categories() -> dict:
    path = Path(__file__).with_name("data") / "job_categories.yaml"
    return yaml.safe_load(path.read_text())


CATEGORY_CRITERIA = {**default_categories(), "overig": "past niet in de andere categorieën"}

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


def questions_for(preferences: dict) -> dict:
    questions = copy.deepcopy(QUESTIONS)
    tracks = preferences.get("tracks") or []
    criteria = {}
    for track in tracks:
        key = track["id"]
        if not key.strip() or key == "overig" or key in criteria:
            raise ValueError("track ids must be unique, nonempty and not 'overig'")
        criteria[key] = ": ".join([track["name"], ", ".join(track.get("keywords") or [])])
    questions["category"]["criteria"] = {
        **(criteria or default_categories()),
        "overig": "past niet in de andere categorieën",
    }
    return questions


def question_version(questions: dict) -> str:
    raw = QUESTION_VERSION + json.dumps(questions, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


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


def compose_state(job, profile_excerpt: str = "", *, preferences: dict | None = None) -> str:
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
    preferences = preferences if preferences is not None else profile.read_preferences()
    parts += [
        "Voorkeuren van deze gebruiker:",
        json.dumps(profile.public_preferences(preferences), ensure_ascii=False),
    ]
    return "\n".join(parts)


def decide(
    state: str, questions: dict | None = None, model: str | None = None
) -> tuple[dict, dict, int]:
    key = api_key()
    if not key:
        raise SystemOneError("RUNWARE_API_KEY ontbreekt")
    effective_questions = (
        questions if questions is not None else questions_for(profile.read_preferences())
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
        "version": question_version(questions if questions is not None else QUESTIONS),
        "fit": fit_value,
        "category": category.get("choice"),
        "category_probabilities": category.get("probabilities"),
        "confidence": confidence,
        "noul": noul,
        "escalate": confidence < confidence_threshold(),
    }


def signals_key(
    job, profile_excerpt: str, *, preferences: dict | None = None, questions: dict | None = None
) -> str:
    preferences = preferences if preferences is not None else profile.read_preferences()
    questions = questions if questions is not None else questions_for(preferences)
    raw = json.dumps(
        {
            "questions": question_version(questions),
            "content": job["content_hash"] or "",
            "profile": profile_excerpt,
            "preferences": profile.public_preferences(preferences),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:20]
