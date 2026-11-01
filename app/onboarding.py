"""Domain logic of the 3-minute start; the router only handles forms and pages."""

from __future__ import annotations

import json
import logging
import re

from . import geo, profile, ranking, runtime, store
from .workflows import _budget_reached

log = logging.getLogger("jobcraft")

# A role someone asked for outweighs incidental keyword matches.
CHOSEN_ROLE_WEIGHT = 4.0
MAX_QUESTIONS = 3
SWIPE_COUNT = 10
ANSWERS_HEADING = "Uit het gesprek"
ROLES_HEADING = "Gewenste functies"
HOURS = {"any": (None, None), "tot24": (8, 24), "24-32": (24, 32), "32-40": (32, 40)}
TRAVEL_CHOICES = (15, 30, 45, 60, 90)
SALARY_CHOICES = (2500, 3000, 3500, 4000, 5000, 6000)
LEVEL_WORDS = {"junior": ["junior", "trainee"], "medior": ["medior"], "senior": ["senior"]}
DEALBREAKERS = (
    ("no_phone_main", "Vooral bellen"),
    ("no_structural_evenings", "Vaste avonden of weekenden"),
    ("no_heavy_physical", "Zwaar lichamelijk werk"),
    ("day_work_only", "Werk dat niet overdag is"),
)


def split_terms(raw: str) -> list[str]:
    return list(dict.fromkeys(term.strip() for term in re.split(r"[,\n;]", raw) if term.strip()))[:15]


def _keywords(role: str) -> list[str]:
    """The whole role plus its distinctive words, so 'Projectleider water' finds both."""
    words = [word for word in re.findall(r"[\w-]+", role.lower()) if len(word) >= 5]
    return list(dict.fromkeys([role.lower(), *words]))


def tracks_for(roles: list[str], existing: list[dict]) -> list[dict]:
    known = {track["name"].casefold(): track for track in existing}
    tracks: list[dict] = []
    used: set[str] = set()
    for role in roles:
        kept = known.get(role.casefold())
        if kept:
            tracks.append(kept)
            used.add(kept["id"])
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-") or "functie"
        if slug == "overig" or slug in used:
            slug = f"{slug}-{len(tracks) + 1}"
        used.add(slug)
        tracks.append({"id": slug, "name": role, "weight": CHOSEN_ROLE_WEIGHT, "keywords": _keywords(role)})
    return tracks


def catalogue_count(roles: list[str], veto: list[str], remote: str, levels: list[str]) -> int:
    include = [term for track in tracks_for(roles, []) for term in track["keywords"]]
    exclude = list(veto)
    if remote in ("remote", "hybrid"):
        exclude.append("geen thuiswerk")
    exclude_titles = [
        word for level, words in LEVEL_WORDS.items() if levels and level not in levels for word in words
    ]
    return store.count_catalogue(include, exclude, exclude_titles)


def minutes_from(raw: object) -> int:
    return int(raw) if str(raw).isdigit() else 30


def save_home(place: str, minutes: int, car: bool) -> bool:
    """Store the home and refresh travel estimates; False when the place is unknown."""
    key = " ".join(place.split()).lower()
    if key not in geo.PLACES:
        return False
    preferences = profile.read_preferences()
    lat, lon = geo.PLACES[key]
    minutes = max(5, min(180, minutes))
    home = {
        "label": geo.PLACE_LABELS[key],
        "lat": lat,
        "lon": lon,
        "bike_minutes": minutes,
        "transit_minutes": minutes,
        "car_available": car,
    }
    if preferences["home"] != home:
        preferences["home"] = home
        profile.write_preferences(preferences)
        ranking.refresh_travel()
    return True


def pick_swipes() -> list[int]:
    """The best fits with different employers, plus a few further down for contrast."""
    rows = [
        row
        for row in store.list_jobs_page(400, 0, "new", sort="rank", within_travel=True)
        if not row["vote"]
    ]
    top = max(SWIPE_COUNT - 3, 1)
    chosen: list = []
    seen: set[str] = set()

    def take(candidates, amount: int) -> None:
        for row in candidates:
            if len(chosen) >= amount:
                return
            company = (row["company"] or "").casefold()
            first_word = (row["title"] or "").casefold().split(" ")[0]
            if company in seen or first_word in seen or row in chosen:
                continue
            seen.update(item for item in (company, first_word) if item)
            chosen.append(row)

    take(rows, top)
    take(rows[len(rows) // 3 :], SWIPE_COUNT)
    if len(chosen) < SWIPE_COUNT:  # A small catalogue: variety matters less than having enough.
        seen.clear()
        take(rows, SWIPE_COUNT)
    return [row["id"] for row in chosen]


def swipe_queue() -> list[int]:
    try:
        return [int(item) for item in json.loads(store.meta_get("start_swipe") or "[]")]
    except (TypeError, ValueError):
        return []


def exchanges_saved() -> list[dict]:
    try:
        return list(json.loads(store.meta_get("start_questions") or "[]"))
    except (TypeError, ValueError):
        return []


def question_prompt(exchanges: list[dict]) -> str:
    """Only confirmed public profile text, public wishes and public vacancy titles."""
    votes = store.list_votes()
    titles = {
        "ja": [row["title"] for row in votes["up"]][:10],
        "nee": [row["title"] for row in votes["down"]][:10],
    }
    return (
        "## Profiel\n"
        f"{profile.read_public_excerpt(4000) or '(nog leeg)'}\n\n"
        "## Keuzes\n"
        f"{json.dumps(profile.public_preferences(profile.read_preferences()), ensure_ascii=False)}\n\n"
        "## Reacties op vacatures (alleen titels)\n"
        f"{json.dumps(titles, ensure_ascii=False)}\n\n"
        "## Eerdere vragen en antwoorden\n"
        f"{json.dumps(exchanges, ensure_ascii=False)}\n\n"
        "Stel nu de volgende vraag."
    )


def ask_next(exchanges: list[dict]) -> bool:
    """Add the next question; False when the AI is unavailable or the budget is spent."""
    manager = runtime.manager
    if manager is None or manager.client is None or _budget_reached():
        return False
    try:
        text, usage = manager.generate(question_prompt(exchanges), agent="onboarding")
    except Exception as exc:
        log.warning("start question failed: %s", type(exc).__name__)
        return False
    store.record_usage(
        None,
        "onboarding",
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        float(usage.get("cost_usd") or 0.0),
        agent="onboarding",
    )
    question = " ".join(text.split())[:400]
    if not question:
        return False
    exchanges.append({"question": question, "answer": None})
    store.meta_set("start_questions", json.dumps(exchanges, ensure_ascii=False))
    return True

