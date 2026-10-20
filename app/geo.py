"""Travel-time estimates from the configured home base (Amsterdam by default).

Travel budgets come from the account's preferences; the legacy defaults are
about 30 minutes by e-bike or 35 minutes by public transport. Exact door-to-door
planning is not available offline, so this module uses a documented approximation:

- e-bike: 18 km/h average through Amsterdam traffic;
- public transport: walk/wait overhead plus tram/metro/train speed, with a
  shorter overhead for places on a direct rail line.

Jobs with coordinates (for example the Greenjobs API) are measured directly.
Everything else is matched against a gazetteer of place names around Amsterdam.
Unknown locations deliberately return ``in_range=None``: the matcher should not
veto a job just because a source does not publish a location.
"""

from __future__ import annotations

import csv
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from . import config, tenancy

HOME = {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041}
BIKE_MINUTES = 30
TRANSIT_MINUTES = 35
BIKE_KMH = 18.0

# Gazetteer: place name -> (lat, lon). Used when a source has no coordinates.
PLACES: dict[str, tuple[float, float]] = {
    "amsterdam": (52.3676, 4.9041),
    "amstelveen": (52.3114, 4.8701),
    "diemen": (52.3395, 4.9625),
    "duivendrecht": (52.3258, 4.9394),
    "ouderkerk aan de amstel": (52.2953, 4.9077),
    "weesp": (52.3075, 5.0417),
    "muiden": (52.3306, 5.0694),
    "badhoevedorp": (52.3403, 4.7806),
    "schiphol": (52.3105, 4.7683),
    "hoofddorp": (52.3025, 4.6889),
    "aalsmeer": (52.2594, 4.7611),
    "uithoorn": (52.2325, 4.8269),
    "kudelstaart": (52.2433, 4.7536),
    "nieuw-vennep": (52.2644, 4.6292),
    "zaandam": (52.4389, 4.8289),
    "zaandijk": (52.4744, 4.8064),
    "krommenie": (52.4994, 4.7656),
    "wormerveer": (52.4922, 4.7917),
    "purmerend": (52.505, 4.9597),
    "edam": (52.5122, 5.045),
    "volendam": (52.4947, 5.0706),
    "monnickendam": (52.4581, 5.0361),
    "landsmeer": (52.4311, 4.9158),
    "oostzaan": (52.4403, 4.8767),
    "watergang": (52.3958, 4.9556),
    "broek in waterland": (52.4239, 4.9958),
    "ilpendam": (52.4528, 4.9528),
    "marken": (52.4592, 5.1008),
    "haarlem": (52.3874, 4.6462),
    "heemstede": (52.35, 4.62),
    "zandvoort": (52.3714, 4.5333),
    "ijmuiden": (52.4604, 4.6106),
    "beverwijk": (52.4844, 4.6594),
    "heemskerk": (52.5111, 4.6714),
    "castricum": (52.5467, 4.6708),
    "uitgeest": (52.5306, 4.71),
    "alkmaar": (52.6324, 4.7539),
    "hoorn": (52.6425, 5.0597),
    "hilversum": (52.2292, 5.1667),
    "bussum": (52.2733, 5.1611),
    "naarden": (52.2964, 5.1617),
    "huizen": (52.2961, 5.2411),
    "blaricum": (52.2733, 5.2333),
    "laren": (52.2564, 5.2333),
    "eemnes": (52.2539, 5.2661),
    "almere": (52.3708, 5.2144),
    "almere poort": (52.355, 5.15),
    "almere haven": (52.34, 5.22),
    "abcoude": (52.2717, 4.9697),
    "baambrugge": (52.2583, 4.9556),
    "vinkeveen": (52.2133, 4.9306),
    "mijdrecht": (52.2064, 4.8664),
    "wilnis": (52.1972, 4.9036),
    "breukelen": (52.1711, 4.9964),
    "maarssen": (52.1408, 5.0403),
    "loosdrecht": (52.2014, 5.0806),
    "amersfoort": (52.1561, 5.3878),
    "utrecht": (52.0907, 5.1214),
    "nieuwegein": (52.03, 5.08),
    "leiden": (52.1601, 4.497),
    "den haag": (52.0705, 4.3007),
    "rotterdam": (51.9244, 4.4777),
    "zwolle": (52.5168, 6.083),
    "apeldoorn": (52.2114, 5.9699),
    "nijmegen": (51.8426, 5.8546),
    "groningen": (53.2194, 6.5665),
    "tilburg": (51.5606, 5.0913),
    "eindhoven": (51.4416, 5.4697),
}

# Places with a direct, frequent rail/metro connection: lower transit overhead.
RAIL_PLACES = {
    "amsterdam",
    "schiphol",
    "hoofddorp",
    "haarlem",
    "zaandam",
    "weesp",
    "almere",
    "almere poort",
    "almere haven",
    "hilversum",
    "bussum",
    "naarden",
    "alkmaar",
    "hoorn",
    "purmerend",
    "amersfoort",
    "utrecht",
    "nieuwegein",
    "leiden",
    "breukelen",
    "maarssen",
    "diemen",
    "duivendrecht",
}

GAZETTEER_FILE = Path(__file__).with_name("data") / "places_nl.csv"


def _load_gazetteer() -> None:
    try:
        with GAZETTEER_FILE.open(newline="") as handle:
            for row in csv.DictReader(handle):
                name = (row.get("name") or "").strip().lower()
                if not name:
                    continue
                PLACES[name] = (float(row["lat"]), float(row["lon"]))
                if row.get("rail") == "1":
                    RAIL_PLACES.add(name)
    except (OSError, KeyError, ValueError):
        return


_load_gazetteer()

AMSTERDAM_DISTRICTS = (
    "noord",
    "zuid",
    "oost",
    "west",
    "centrum",
    "nieuw-west",
    "zuidoost",
    "ijburg",
    "de pijp",
    "pijp",
    "watergraafsmeer",
    "bos en lommer",
    "westerpark",
    "oud-west",
    "de baarsjes",
    "slotervaart",
    "geuzenveld",
    "osdorp",
    "bijlmer",
    "gaasperdam",
    "sloterdijk",
    "amstel",
    "rivierenbuurt",
    "indische buurt",
    "jordaan",
    "oude west",
    "kinkerbuurt",
    "staatsliedenbuurt",
    "frederikspark",
    "zuidas",
    "buitenveldert",
    "driemond",
)

AUTO_PATTERN = re.compile(
    r"bedrijfsauto|dienstauto|auto van de zaak|rijdt? (?:langs|naar) (?:verschillende )?(?:locaties|vestigingen)"
    r"|mobiel werk|(?:in het bezit van|hebt? een) rijbewijs|eigen vervoer",
    re.I,
)


@lru_cache(maxsize=8)
def _settings_cached(mtime: float, size: int, path: str = "") -> dict:
    try:
        preferences = json.loads(
            Path(path or (config.profile_dir() / "preferences.json")).read_text()
        )
        home_data = preferences.get("home") or {}
        return {
            "label": str(home_data.get("label") or HOME["label"]),
            "lat": float(home_data.get("lat", HOME["lat"])),
            "lon": float(home_data.get("lon", HOME["lon"])),
            "bike_minutes": int(home_data.get("bike_minutes") or BIKE_MINUTES),
            "transit_minutes": int(home_data.get("transit_minutes") or TRANSIT_MINUTES),
        }
    except (OSError, ValueError, TypeError):
        return {**HOME, "bike_minutes": BIKE_MINUTES, "transit_minutes": TRANSIT_MINUTES}


def home() -> dict:
    """Home base and travel budgets from preferences.json (cached by mtime)."""
    path = config.profile_dir() / "preferences.json"
    try:
        stat = path.stat()
        return _settings_cached(stat.st_mtime, stat.st_size, str(path))
    except OSError:
        if tenancy.user_id() != tenancy.LEGACY_ID:
            return {
                **HOME,
                "label": "Nog niet ingesteld",
                "lat": 0,
                "lon": 0,
                "bike_minutes": BIKE_MINUTES,
                "transit_minutes": TRANSIT_MINUTES,
            }
        return {**HOME, "bike_minutes": BIKE_MINUTES, "transit_minutes": TRANSIT_MINUTES}


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _minutes(distance_km: float, rail: bool) -> tuple[int, int]:
    bike = distance_km / (BIKE_KMH / 60.0)
    if rail:
        transit = 14 + distance_km * 0.9
    elif distance_km <= 8:
        transit = 12 + distance_km * 1.8
    else:
        transit = 18 + distance_km * 1.1
    return int(round(bike)), int(round(transit))


def _contains_place(text: str, place: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(place)}(?!\w)", text))


def _find_place(text: str) -> str | None:
    if not text:
        return None
    for district in AMSTERDAM_DISTRICTS:
        if _contains_place(text, district) and _contains_place(text, "amsterdam"):
            return "amsterdam"
    best = None
    for place in PLACES:
        if _contains_place(text, place) and (best is None or len(place) > len(best)):
            best = place
    return best


# Representative in-city distance when home and job are both "Amsterdam":
# the centre-to-centre distance of 0 km would otherwise suggest a 0-minute
# commute anywhere in the city.
IN_CITY_KM = 4.0


def assess(
    location: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    description: str | None = None,
    title: str | None = None,
) -> dict:
    """Estimate travel from home; never raises, always returns a dict.

    Only the explicit ``location`` (or coordinates) is used for placement. A
    city mentioned in the description is not treated as the job location; that
    would misplace remote or organisation-level mentions.
    """
    auto = bool(AUTO_PATTERN.search(_norm(f"{description or ''} {title or ''}")))
    settings = home()
    if tenancy.user_id() != tenancy.LEGACY_ID and (settings["lat"], settings["lon"]) == (0, 0):
        return {
            "place": location or "onbekend",
            "distance_km": None,
            "bike_min": None,
            "transit_min": None,
            "in_range": None,
            "auto": auto,
            "source": "unknown",
            "label": "Stel je woonplaats in via je profiel",
        }
    source = "unknown"
    place = None
    distance = None
    in_city = False

    if lat is not None and lon is not None:
        try:
            distance = haversine_km(settings["lat"], settings["lon"], float(lat), float(lon))
            place = _find_place(_norm(location)) or "onbekend"
            source = "coords"
        except (TypeError, ValueError):
            distance = None

    if distance is None:
        place = _find_place(_norm(location))
        if place:
            plat, plon = PLACES[place]
            distance = haversine_km(settings["lat"], settings["lon"], plat, plon)
            source = "place"

    if distance is None:
        return {
            "place": place or "onbekend",
            "distance_km": None,
            "bike_min": None,
            "transit_min": None,
            "in_range": None,
            "auto": auto,
            "source": source,
            "label": "locatie onbekend",
        }

    if place == "amsterdam" and distance < 1.0:
        distance = IN_CITY_KM
        in_city = True

    rail = bool(place and place in RAIL_PLACES)
    bike_min, transit_min = _minutes(distance, rail)
    in_range = bike_min <= settings["bike_minutes"] or transit_min <= settings["transit_minutes"]
    prefix = "in Amsterdam · " if in_city else ""
    if in_range:
        label = f"{prefix}~{bike_min} min fiets / ~{transit_min} min OV"
    elif auto:
        label = f"buiten fiets/OV-bereik (~{transit_min} min OV) · met bedrijfsauto"
    else:
        label = f"te ver · ~{bike_min} min fiets / ~{transit_min} min OV"
    return {
        "place": (place or "").title() or "onbekend",
        "distance_km": round(distance, 1),
        "bike_min": bike_min,
        "transit_min": transit_min,
        "in_range": in_range,
        "auto": auto,
        "source": source,
        "label": label,
    }
