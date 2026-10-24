"""Offline travel estimates using an account home and the packaged place catalogue.

Bike and transit times are approximations, not route planning. Unknown homes or
locations never veto a vacancy. Owning a car is distinct from employer transport;
without a driving-time budget it does not bypass the bike/transit limits.
"""

from __future__ import annotations

import csv
import json
import math
import re
from functools import lru_cache
from pathlib import Path

BIKE_MINUTES = 30
TRANSIT_MINUTES = 35
BIKE_KMH = 18.0
IN_CITY_KM = 4.0
TRAVEL_VERSION = "account-home-v2"
GAZETTEER_FILE = Path(__file__).with_name("data") / "places_nl.csv"


def load_gazetteer(path: Path) -> tuple[dict, set, dict]:
    """Load completely before publishing; missing data leaves manual coordinates usable."""
    places, rail, districts = {}, set(), {}
    try:
        handle = path.open(newline="", encoding="utf-8")
    except FileNotFoundError:
        return places, rail, districts
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
            if row.get("rail") == "1":
                rail.add(name)
    if not set(districts) <= set(places):
        raise ValueError("Gazetteer district refers to an unknown city")
    return places, rail, districts


def valid_coordinates(lat: float, lon: float) -> bool:
    return math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180


PLACES, RAIL_PLACES, DISTRICTS = load_gazetteer(GAZETTEER_FILE)
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
    return {
        "label": str(data.get("label") or "Nog niet ingesteld"),
        "lat": float(data.get("lat") or 0),
        "lon": float(data.get("lon") or 0),
        "bike_minutes": int(data.get("bike_minutes", BIKE_MINUTES)),
        "transit_minutes": int(data.get("transit_minutes", TRANSIT_MINUTES)),
        "car_available": bool(data.get("car_available", False)),
    }


def home() -> dict:
    """Read the current account's preferences; cache by content, never request identity."""
    from . import profile

    data = profile.read_preferences().get("home") or {}
    return dict(_settings_cached(json.dumps(data, sort_keys=True)))


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def _minutes(distance_km: float, rail: bool) -> tuple[int, int]:
    bike = distance_km / (BIKE_KMH / 60.0)
    transit = (
        14 + distance_km * 0.9
        if rail
        else (12 + distance_km * 1.8 if distance_km <= 8 else 18 + distance_km * 1.1)
    )
    return int(round(bike)), int(round(transit))


def _contains_place(text: str, place: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(place)}(?!\w)", text))


def _find_place(text: str) -> str | None:
    text = _norm(text)
    for city, districts in DISTRICTS.items():
        if _contains_place(text, city) and any(_contains_place(text, item) for item in districts):
            return city
    return next(
        (place for place in sorted(PLACES, key=len, reverse=True) if _contains_place(text, place)),
        None,
    )


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
    auto = employer_transport(f"{description or ''}\n{title or ''}")
    result = {
        "place": location or "onbekend",
        "distance_km": None,
        "bike_min": None,
        "transit_min": None,
        "in_range": None,
        "auto": auto,
        "source": "unknown",
        "car_available": bool(settings.get("car_available")),
        "label": "locatie onbekend",
    }
    if (settings["lat"], settings["lon"]) == (0, 0):
        return result | {"label": "Stel je woonplaats in via je profiel"}
    place = _find_place(location or "")
    distance, source = None, "unknown"
    if lat is not None and lon is not None:
        try:
            latitude, longitude = float(lat), float(lon)
            if valid_coordinates(latitude, longitude):
                distance = haversine_km(settings["lat"], settings["lon"], latitude, longitude)
                source = "coords"
        except (ValueError, TypeError):
            pass
    if distance is None and place:
        distance = haversine_km(settings["lat"], settings["lon"], *PLACES[place])
        source = "place"
    if distance is None:
        return result
    in_city = source == "place" and distance < 1 and place == _find_place(settings["label"])
    if in_city:
        distance = IN_CITY_KM
    bike_min, transit_min = _minutes(distance, bool(place and place in RAIL_PLACES))
    in_range = bike_min <= settings["bike_minutes"] or transit_min <= settings["transit_minutes"]
    prefix = f"in {settings['label']} · " if in_city else ""
    if in_range:
        label = f"{prefix}~{bike_min} min fiets / ~{transit_min} min OV"
    elif auto:
        label = f"buiten fiets/OV-bereik (~{transit_min} min OV) · met bedrijfsauto"
    else:
        label = f"te ver · ~{bike_min} min fiets / ~{transit_min} min OV"
    return result | {
        "place": (place or location or "onbekend").title(),
        "distance_km": round(distance, 1),
        "bike_min": bike_min,
        "transit_min": transit_min,
        "in_range": in_range,
        "source": source,
        "label": label,
    }
