"""Door-to-door minutes by bike, transit and car between two points in the Netherlands.

Models, not route planning: straight-line distance with a detour factor, and for
transit the best of a bus or tram and a train between the nearest stations.
"""

from __future__ import annotations

import csv
import math
from functools import lru_cache
from pathlib import Path

BIKE_KMH = {"fiets": 15.0, "ebike": 20.0}
# Roads and cycle paths are longer than the straight line between two places.
BIKE_ROUTE_FACTOR = 1.2
CAR_ROUTE_FACTOR = 1.3
# Dutch railway stations, from the CC0 open data of Rijden de Treinen (rijdendetreinen.nl).
STATIONS_FILE = Path(__file__).with_name("data") / "stations_nl.csv"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def load_stations(path: Path) -> list[tuple[float, float, bool]]:
    """(lat, lon, intercity) per station; without the file transit falls back to buses."""
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return [(float(row["lat"]), float(row["lon"]), row["kind"] == "ic") for row in csv.DictReader(handle)]
    except FileNotFoundError:
        return []


STATIONS = load_stations(STATIONS_FILE)


def bus_minutes(distance_km: float) -> float:
    return 12 + distance_km * 1.8 if distance_km <= 8 else 18 + distance_km * 1.1


@lru_cache(maxsize=4096)
def _stations_near(lat: float, lon: float) -> tuple[tuple[float, tuple], ...]:
    """The three nearest stations within 12 km, with their straight-line distance."""
    found = sorted((haversine_km(lat, lon, s[0], s[1]), s) for s in STATIONS)
    return tuple(item for item in found[:3] if item[0] <= 12)


def _train_minutes(start: tuple, end: tuple) -> float:
    km = haversine_km(start[0], start[1], end[0], end[1])
    # Intercity speed, a change on long trips, and slower or rarer trains at small stations.
    return 3 + km * 0.72 + max(0.0, km - 60) * 0.15 + 6 * sum(not station[2] for station in (start, end))


def transit_minutes(home: tuple[float, float], work: tuple[float, float], distance_km: float) -> int:
    """Bus or tram, or: walk or cycle to a station, the train, then walk or a bus to work."""
    best = bus_minutes(distance_km)
    for to_station, first in _stations_near(*home):
        reach = min(to_station * 1.25 / 5 * 60, 3 + to_station * 1.2 / 15 * 60)
        for from_station, last in _stations_near(*work):
            if first is last:
                continue
            leave = min(from_station * 1.25 / 5 * 60, 7 + from_station * 1.3 / 20 * 60)
            best = min(best, 5 + reach + _train_minutes(first, last) + leave)
    return round(best)


def bike_minutes(distance_km: float, kind: str) -> int:
    return round(distance_km * BIKE_ROUTE_FACTOR / (BIKE_KMH[kind] / 60.0))


def car_minutes(distance_km: float) -> int:
    # Town streets first, then the motorway; five minutes to park and walk.
    road = distance_km * CAR_ROUTE_FACTOR
    return round(5 + min(road, 6.0) / 30 * 60 + max(0.0, road - 6.0) / 85 * 60)
