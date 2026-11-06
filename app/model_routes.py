"""Which model does what. Admins choose per task; the choice holds for web and worker."""

from __future__ import annotations

import json
import re
import time

import httpx

from . import config, store

SETTING = "model_routes"
# Task id, what an admin sees, what it does.
ROLES = (
    ("doc_researcher", "Werkgeveronderzoek", "Pluist de vacature en openbare werkgeverspagina's uit. Krijgt niets over de gebruiker."),
    ("doc_strategist", "Sollicitatieplan", "Koppelt wat de werkgever zoekt aan het bewijs uit het cv en bedenkt invalshoeken."),
    ("cv_writer", "CV schrijven", "Schrijft het cv op maat."),
    ("letter_writer", "Motivatiebrief schrijven", "Schrijft de brief. Hier maakt een goed model het meeste verschil."),
    ("doc_critic", "Recruitercontrole", "Leest cv en brief kritisch na en wijst verzinsels aan."),
    ("vacancy_reader", "Vacaturetekst opmaken", "Maakt van rommelige tekst een leesbare vacature. Eén keer per tekst; een snel model is genoeg."),
    ("career_coach", "Sterktes en zwaktes", "De eerlijke analyse op de profielpagina."),
    ("cv_reader", "Cv inlezen", "Zet een geplakt of geüpload cv om naar onderdelen. Letterlijk; een snel model is genoeg."),
    ("cv_coach", "Cv-coach", "Leest Mijn cv mee en zet vragen, tegenstrijdigheden en sterkere zinnen bij de onderdelen."),
    ("matcher", "Vacature beoordelen", "De diepere beoordeling van twijfelgevallen."),
    ("job_gap", "Wat ontbreekt", "De vergelijking tussen vacature en profiel."),
    ("job_prep", "Gesprek voorbereiden", "Vragen en voorbereiding voor het gesprek."),
    ("onboarding", "Startvragen", "De korte vervolgvragen na de cv-upload."),
    ("interviewer", "Gesprek", "Het doorlopende gesprek."),
    ("writer", "Profiel samenvatten", "Maakt het profiel uit gesprekken."),
)
_ROLE_IDS = {role[0] for role in ROLES}
# The same tasks in the order a person meets them: reading vacancies, writing an application, the profile.
STAGES = (
    ("Vacatures lezen en beoordelen", ("vacancy_reader", "matcher", "job_gap", "job_prep")),
    ("Sollicitatie schrijven", ("doc_researcher", "doc_strategist", "cv_writer", "letter_writer", "doc_critic")),
    ("Profiel", ("cv_reader", "onboarding", "interviewer", "writer", "career_coach", "cv_coach")),
)
_MODEL_ID = re.compile(r"[\w./:@-]{1,120}")
_CACHE_SECONDS = 600
_available: tuple[float, list[str]] = (0.0, [])


def routes() -> dict[str, str]:
    try:
        data = json.loads(store.meta_get(SETTING) or "{}")
    except ValueError:
        return {}
    return {key: value for key, value in data.items() if key in _ROLE_IDS and isinstance(value, str)}


def model_for(role: str) -> str:
    return routes().get(role) or config.role_model(role)


def valid_model(value: str) -> bool:
    return bool(_MODEL_ID.fullmatch(value))


def save(chosen: dict[str, str]) -> None:
    """Empty means: use the default. Raises ValueError rather than saving a broken id."""
    clean = {}
    for role, value in chosen.items():
        value = value.strip()
        if role not in _ROLE_IDS or not value:
            continue
        if not valid_model(value):
            raise ValueError(role)
        clean[role] = value
    store.meta_set(SETTING, json.dumps(clean))


def available_models() -> list[str]:
    """What the provider offers, for suggestions only; an empty list must not block saving."""
    global _available
    fetched, models = _available
    if time.monotonic() - fetched < _CACHE_SECONDS and models:
        return models
    if not config.LLM_BASE_URL or not config.LLM_API_KEY:
        return []
    try:
        response = httpx.get(
            config.LLM_BASE_URL.rstrip("/") + "/models",
            headers={"authorization": f"Bearer {config.LLM_API_KEY}"},
            timeout=10,
        )
        response.raise_for_status()
        models = sorted(str(item["id"]) for item in response.json().get("data") or [])
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return models
    _available = (time.monotonic(), models)
    return models
