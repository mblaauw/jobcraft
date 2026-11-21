"""The list of Dutch places: where they are, which have a station, and their districts."""

from __future__ import annotations

import csv
import math
from pathlib import Path


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
