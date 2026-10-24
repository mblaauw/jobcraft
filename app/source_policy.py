"""Pure shared-catalogue metadata and per-account selection policy."""

from functools import lru_cache
from pathlib import Path

import yaml

from . import geo


@lru_cache(maxsize=1)
def seed_metadata() -> dict[str, dict]:
    path = Path(__file__).resolve().parent.parent / "seeds" / "sources.yaml"
    payload = yaml.safe_load(path.read_text()) or {}
    return {
        item["id"]: {key: item[key] for key in ("scope", "regions") if key in item}
        for item in payload.get("sources", [])
    }


def effective_metadata(source: dict) -> dict:
    defaults = seed_metadata().get(source["id"], {})
    return {"scope": "national", "regions": [], **defaults, **source}


def selection(source: dict, home: dict, override: bool | None = None) -> dict:
    source = effective_metadata(source)
    if override is not None:
        return {"selected": override, "explicit": True, "reason": "Je eigen keuze"}
    if source["scope"] != "regional":
        return {"selected": True, "explicit": False, "reason": "Landelijke bron"}
    lat, lon = home.get("lat", 0), home.get("lon", 0)
    if not lat and not lon:
        return {"selected": True, "explicit": False, "reason": "Regiobron; stel je woonplaats in"}
    points = [geo.PLACES[name.lower()] for name in source["regions"] if name.lower() in geo.PLACES]
    nearby = any(geo.haversine_km(lat, lon, *point) <= 40 for point in points)
    return {
        "selected": nearby,
        "explicit": False,
        "reason": "Regio binnen 40 km" if nearby else "Regio buiten bereik of onbekend",
    }
