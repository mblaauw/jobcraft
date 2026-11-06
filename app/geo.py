"""Offline travel estimates using an account home and the packaged place catalogue.

Bike, transit and car times are approximations, not route planning. Unknown homes
or locations never veto a vacancy, and neither does distance for fully remote work
or work with a company car. Your own car counts only through its own minutes.
"""

from __future__ import annotations

import contextlib
import contextvars
import csv
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from .commute import bike_minutes, bus_minutes, car_minutes, haversine_km, transit_minutes

BIKE_MINUTES = 30
TRANSIT_MINUTES = 35
BIKE_WORDS = {"fiets": "fiets", "ebike": "e-bike"}
IN_CITY_KM = 4.0
# Stored estimates hold minutes per way of travelling, never a verdict on your budgets.
TRAVEL_VERSION = "travel-times-v5"
GAZETTEER_FILE = Path(__file__).with_name("data") / "places_nl.csv"
# Names vacancies use for a place the catalogue lists under another name.
ALIASES = {
    "den bosch": "'s-hertogenbosch",
    "hoogvliet": "hoogvliet rotterdam",
    "pernis": "rotterdam",
    "zaanstad": "zaandam",
    "haarlemmermeer": "hoofddorp",
    "westland": "naaldwijk",
    "velsen": "ijmuiden",
    "the hague": "den haag",
}


def load_gazetteer(path: Path) -> tuple[dict, set, dict, dict]:
    """Load completely before publishing; missing data leaves manual coordinates usable."""
    places, rail, districts, labels = {}, set(), {}, {}
    try:
        handle = path.open(newline="", encoding="utf-8")
    except FileNotFoundError:
        return places, rail, districts, labels
    with handle:
        for row in csv.DictReader(handle):
            name = (row.get("name") or "").strip().lower()
            if not name:
                continue
            city = (row.get("city") or "").strip().lower()
            if city:
                districts.setdefault(city, set()).add(name)
                continue
            lat, lon = float(row["lat"]), float(row["lon"])
            if not valid_coordinates(lat, lon) or name in places:
                raise ValueError(f"Invalid or duplicate gazetteer place: {name}")
            places[name] = (lat, lon)
            labels[name] = (row.get("label") or "").strip() or name.title()
            if row.get("rail") == "1":
                rail.add(name)
    if not set(districts) <= set(places):
        raise ValueError("Gazetteer district refers to an unknown city")
    return places, rail, districts, labels


def valid_coordinates(lat: float, lon: float) -> bool:
    return math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180


PLACES, RAIL_PLACES, DISTRICTS, PLACE_LABELS = load_gazetteer(GAZETTEER_FILE)
for _alias, _place in ALIASES.items():
    if _place in PLACES and _alias not in PLACES:
        PLACES[_alias] = PLACES[_place]
        PLACE_LABELS[_alias] = PLACE_LABELS[_place]




def _core_places(path: Path) -> set[str]:
    """Hand-kept towns. Worldwide text needs these: the full list also holds villages
    named Zurich and America, which must not make a foreign vacancy look Dutch."""
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return {
                row["name"].strip().lower()
                for row in csv.DictReader(handle)
                if not (row.get("origin") or "").strip() and not (row.get("city") or "").strip()
            }
    except FileNotFoundError:
        return set()


CORE_PLACES = _core_places(GAZETTEER_FILE)
# Two-letter villages ("Ee") would match stray words in free-form location text.
_MATCHABLE = sorted((place for place in PLACES if len(place) > 2), key=len, reverse=True)
PLACE_PATTERN = (
    re.compile(r"(?<!\w)(?:" + "|".join(re.escape(place) for place in _MATCHABLE) + r")(?!\w)")
    if _MATCHABLE
    else None
)
AUTO_PATTERN = re.compile(
    r"\b(?:bedrijfsauto|dienstauto|auto van de zaak)\b",
    re.I,
)


def employer_transport(text: str) -> bool:
    for clause in re.split(r"[.!?;\n]", text.lower()):
        for match in AUTO_PATTERN.finditer(clause):
            before, after = clause[: match.start()], clause[match.end() :]
            negated_before = re.search(
                r"\b(?:geen|zonder|niet)\s+(?:(?:een|eigen|vaste|persoonlijke|extra|nieuwe|met)\s+)*$",
                before,
            )
            negated_after = re.match(
                r"\s*[,(:-]?\s*(?:(?:is|wordt)\s+)?(?:niet|ontbreekt|geen)\b", after
            )
            unavailable_after = re.match(
                r"\s*[,(:-]?\s*(?:is|wordt)\s+(?:\w+\s+){0,3}niet\s+(?:\w+\s+){0,2}beschikbaar\b",
                after,
            )
            negated_list = re.search(
                r"\b(?:geen|zonder)\s+(?:bedrijfsauto|dienstauto|auto van de zaak)\s+(?:of|en)\s+$",
                before,
            )
            if not (negated_before or negated_after or unavailable_after or negated_list):
                return True
    return False


@lru_cache(maxsize=128)
def _settings_cached(serialized: str) -> dict:
    data = json.loads(serialized)
    car_minutes = int(data.get("car_minutes", 0))
    return {
        "label": str(data.get("label") or "Nog niet ingesteld"),
        "lat": float(data.get("lat") or 0),
        "lon": float(data.get("lon") or 0),
        "bike_minutes": int(data.get("bike_minutes", BIKE_MINUTES)),
        "bike_kind": "fiets" if data.get("bike_kind") == "fiets" else "ebike",
        "transit_minutes": int(data.get("transit_minutes", TRANSIT_MINUTES)),
        "car_minutes": car_minutes,
        "car_available": car_minutes > 0,
    }


_REMEMBERED: contextvars.ContextVar[dict | None] = contextvars.ContextVar("geo_home", default=None)


@contextlib.contextmanager
def remembered_home():
    """Read home once for a whole re-rank instead of once per vacancy."""
    token = _REMEMBERED.set(home())
    try:
        yield
    finally:
        _REMEMBERED.reset(token)


def home() -> dict:
    """Read the current account's preferences; cache by content, never request identity."""
    from . import profile

    if (remembered := _REMEMBERED.get()) is not None:
        return dict(remembered)
    data = profile.read_preferences().get("home") or {}
    return dict(_settings_cached(json.dumps(data, sort_keys=True)))


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


_REMOTE_PLACE = re.compile(r"\b(?:remote|thuiswerk\w*|vanuit huis|home[- ]?based|op afstand)\b")


def fully_remote(location: str | None, text: str | None) -> bool:
    """Work that names no office to travel to: "Remote - Netherlands", or "volledig thuis"."""
    from .scoring.wishes import remote_kind

    return bool(_REMOTE_PLACE.search(_norm(location))) or remote_kind((text or "").lower()) == "Volledig thuis"


def reach_line(settings: dict) -> str:
    """'45 min e-bike of 45 min OV': the budgets a person set, in their words."""
    parts = [
        f"{minutes} min {word}"
        for minutes, word in (
            (settings.get("bike_minutes", 0), BIKE_WORDS.get(settings.get("bike_kind") or "ebike", "e-bike")),
            (settings.get("transit_minutes", 0), "OV"),
            (settings.get("car_minutes", 0), "auto"),
        )
        if minutes
    ]
    if not parts:
        return "geen manier van reizen gekozen"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " of " + parts[-1]


def budget(minutes: int, highest: int) -> int:
    """A travel budget as stored: 0 for a way you do not travel, else 5 up to the highest."""
    return 0 if minutes <= 0 else max(5, min(highest, minutes))


def too_far(travel: dict) -> bool:
    """The one rule for hiding a vacancy on distance: an office out of reach, no way around it."""
    return travel.get("in_range") is False and not travel.get("auto") and not travel.get("remote")


def _ways(settings: dict) -> list[tuple[str, int]]:
    """(stored minutes key, budget) for each way this person travels."""
    kind = settings.get("bike_kind") or "ebike"
    ways = (
        (f"{kind}_min", settings.get("bike_minutes", 0)),
        ("transit_min", settings.get("transit_minutes", 0)),
        ("car_min", settings.get("car_minutes", 0)),
    )
    return [(key, int(minutes)) for key, minutes in ways if minutes and minutes > 0]


def too_far_sql(settings: dict, column: str = "travel_json") -> tuple[str, list]:
    """The SQL twin of too_far over stored times, for the budgets in settings."""
    ways = _ways(settings)
    reachable = " OR ".join(f"json_extract({column},'$.{key}') <= ?" for key, _ in ways) or "0"
    sql = (
        f"(json_extract({column},'$.transit_min') IS NOT NULL"
        f" AND COALESCE(json_extract({column},'$.remote'),0)=0"
        f" AND COALESCE(json_extract({column},'$.auto'),0)=0"
        f" AND NOT ({reachable}))"
    )
    return sql, [minutes for _, minutes in ways]


def _contains_place(text: str, place: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(place)}(?!\w)", text))


@lru_cache(maxsize=8192)
def _find_place(text: str) -> str | None:
    # "Bergen, NH" is how boards write the catalogue's "Bergen (NH)".
    text = re.sub(r",\s*(nh|nb|l|zh|gld)\b", r" (\1)", _norm(text))
    for city, districts in DISTRICTS.items():
        if _contains_place(text, city) and any(_contains_place(text, item) for item in districts):
            return city
    if PLACE_PATTERN is None:
        return None
    return max((match.group(0) for match in PLACE_PATTERN.finditer(text)), key=len, default=None)


_PLACE_LABEL = re.compile(
    r"(?<!\w)(?:(?:standplaats|werklocatie|locatie)\s*[:\-]?|(?:werkplek|plaats|vestiging)\s*[:\-])\s*([^\n|;]{2,60})"
)
_SEEKS_IN = re.compile(
    r"(?<!\w)in ([^\n|;.,]{2,40}?) (?:zoekt|zoeken|is op zoek|zijn op zoek|is looking for|are looking for)(?!\w)"
)
_DOT_PLACE = re.compile(r"· ([^·|\n]{2,30}?) (?:parttime|fulltime|tijdelijk|vast|\d)")


def place_in_text(text: str | None) -> str:
    """The work place a vacancy states itself ("X in Utrecht zoekt", "Standplaats: Delft",
    or "Organisatie, Plaats" as its header), never a place merely mentioned somewhere."""
    text = (text or "").replace("\u2018", "'").replace("\u2019", "'")
    candidates = [match.group(1) for match in _PLACE_LABEL.finditer(_norm_lines(text[:3000]))]
    candidates += [match.group(1) for match in _SEEKS_IN.finditer(_norm(text[:400]))]
    candidates += [match.group(1) for match in _DOT_PLACE.finditer(_norm(text[:600]))]
    # A short header line such as "Stichting X, Amsterdam-Oost": only a bare place after the comma.
    for line in [line for line in text.splitlines()[:3] if len(line) <= 80]:
        candidates += [part for part in re.split(r"[,|·]", line)[1:] if 0 < len(part.split()) <= 2]
    for candidate in candidates:
        place = _find_place(candidate)
        if place:
            return PLACE_LABELS.get(place, "") or place.title()
    return ""


def _norm_lines(value: str) -> str:
    return "\n".join(_norm(line) for line in value.splitlines())


def times(
    location: str | None,
    lat: float | None = None,
    lon: float | None = None,
    description: str | None = None,
    title: str | None = None,
    *,
    home_settings: dict | None = None,
) -> dict:
    """Minutes to a vacancy by each way of travelling; they depend on where you live only."""
    settings = home_settings if home_settings is not None else home()
    text = f"{description or ''}\n{title or ''}"
    result = {
        "place": location or "onbekend",
        "located": bool((location or "").strip()),
        "distance_km": None,
        "fiets_min": None,
        "ebike_min": None,
        "transit_min": None,
        "car_min": None,
        "in_city": False,
        "auto": employer_transport(text),
        "remote": fully_remote(location, text),
        "source": "unknown",
        "home": (settings["lat"], settings["lon"]) != (0, 0),
    }
    if not result["home"]:
        return result
    start = (settings["lat"], settings["lon"])
    place = _find_place(location or "")
    work, source = None, "unknown"
    if lat is not None and lon is not None:
        try:
            if valid_coordinates(float(lat), float(lon)):
                work, source = (float(lat), float(lon)), "coords"
        except (ValueError, TypeError):
            pass
    if work is None and place:
        work, source = PLACES[place], "place"
    if work is None:
        return result
    distance = haversine_km(*start, *work)
    in_city = source == "place" and distance < 1 and place == _find_place(settings["label"])
    if in_city:
        distance = IN_CITY_KM
    transit = round(bus_minutes(distance)) if in_city else transit_minutes(start, work, distance)
    return result | {
        "place": PLACE_LABELS.get(place, "") or (location or "onbekend").title(),
        "distance_km": round(distance, 1),
        "fiets_min": bike_minutes(distance, "fiets"),
        "ebike_min": bike_minutes(distance, "ebike"),
        "transit_min": transit,
        "car_min": car_minutes(distance),
        "in_city": in_city,
        "source": source,
    }


def judge(found: dict, settings: dict) -> dict:
    """Stored times against this person's budgets: in reach, too far, and how to say it."""
    kind = settings.get("bike_kind") or "ebike"
    remote, auto = bool(found.get("remote")), bool(found.get("auto"))
    result = found | {
        "bike_min": found.get(f"{kind}_min"),
        "in_range": None,
        "too_far": False,
        "car_available": bool(settings.get("car_minutes")),
    }
    if found.get("transit_min") is None:
        if remote:
            label = "volledig thuiswerk"
        elif not found.get("home", True):
            label = "Stel je woonplaats in via je profiel"
        else:
            label = "reistijd onbekend" if found.get("located") else "locatie onbekend"
        return result | {"label": label}
    in_range = any(found.get(key) is not None and found[key] <= budget for key, budget in _ways(settings))
    times = f"~{result['bike_min']} min {BIKE_WORDS[kind]} / ~{found['transit_min']} min OV"
    if settings.get("car_minutes", 0) > 0:
        times += f" / ~{found['car_min']} min auto"
    if in_range:
        label = (f"in {settings['label']} · " if found.get("in_city") else "") + times
    elif remote:
        label = f"volledig thuiswerk · kantoor {times}"
    elif auto:
        label = f"buiten je reisbereik (~{found['transit_min']} min OV) · met bedrijfsauto"
    else:
        label = f"te ver · {times}"
    result = result | {"in_range": in_range, "label": label}
    return result | {"too_far": too_far(result)}


def assess(
    location: str | None,
    lat: float | None = None,
    lon: float | None = None,
    description: str | None = None,
    title: str | None = None,
    *,
    home_settings: dict | None = None,
) -> dict:
    settings = home_settings if home_settings is not None else home()
    found = times(location, lat, lon, description, title, home_settings=settings)
    return judge(found, settings)
