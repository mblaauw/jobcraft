"""The reach above the vacancy list: try other minutes or ways of travelling, then keep them.

Stored travel times never depend on budgets, so every stop of the slider is one SQL
count away. A reach from the list stays in the address until you save it as yours.
"""

from __future__ import annotations

import itertools
import json

from . import geo

STOPS = (15, 30, 45, 60, 75, 90, 105, 120)
MODES = (("fiets", "Fiets", "bike"), ("ebike", "E-bike", "bike"), ("ov", "OV", "train-front"), ("auto", "Auto", "car"))
LIST_PARAMS = ("reis", "met", "vanaf")


def saved_modes(home: dict) -> list[str]:
    modes = [home.get("bike_kind") or "ebike"] if home.get("bike_minutes") else []
    modes += ["ov"] if home.get("transit_minutes") else []
    return modes + (["auto"] if home.get("car_minutes") else [])


def longest(home: dict) -> int:
    return max(home.get("bike_minutes") or 0, home.get("transit_minutes") or 0, home.get("car_minutes") or 0)


def budgets(minutes: int, modes: list[str]) -> dict:
    """One time budget for each chosen way of travelling, as stored in a home."""
    cycling = "ebike" in modes or "fiets" in modes
    return {
        "bike_minutes": minutes if cycling else 0,
        "bike_kind": "ebike" if "ebike" in modes or not cycling else "fiets",
        "transit_minutes": minutes if "ov" in modes else 0,
        "car_minutes": minutes if "auto" in modes else 0,
    }


def _stop(minutes: int) -> int:
    return min(STOPS, key=lambda stop: abs(stop - minutes))


def _key(minutes: int, modes: list[str]) -> str:
    return f"{minutes}:{','.join(modes)}"


def from_query(query, home: dict) -> dict:
    """The reach a list shows: your own, unless the slider or the ways were changed."""
    own_modes = saved_modes(home)
    base = _key(_stop(longest(home) or 30), own_modes)
    minutes, modes = _stop(longest(home) or 30), own_modes
    if "met" in query:
        # Ticked boxes repeat "met"; links carry them joined ("ebike,ov", or "geen").
        picked = {part for value in query.getlist("met") for part in value.split(",")}
        modes = [value for value, _, _ in MODES if value in picked]
        if "ebike" in modes and "fiets" in modes:
            modes.remove("fiets")
    raw = query.get("reis", "")
    if raw.isdigit():
        minutes = _stop(int(raw))
    # Untouched controls mean your own reach, also when its minutes differ per way.
    changed = query.get("vanaf", base) != base or _key(minutes, modes) != base
    settings = home | budgets(minutes, modes) if changed else home
    return {"settings": settings, "minutes": minutes, "modes": modes, "changed": changed, "base": base}


def view(reach: dict, counts: list[int], visible: int) -> dict:
    """What the slider needs: counts per stop, bar heights, and the next step's gain."""
    by_stop = dict(zip(STOPS, counts[1:], strict=True))
    if not reach["changed"]:
        by_stop[reach["minutes"]] = visible  # your own reach, minutes per way and all
    steps = [max(0, count - previous) for previous, count in itertools.pairwise(counts)]
    tallest = max(steps) or 1
    return {
        **reach,
        "count": visible,
        "counts_json": json.dumps({str(stop): count for stop, count in by_stop.items()}),
        "bars": [{"minutes": stop, "height": round(100 * step / tallest)} for stop, step in zip(STOPS, steps, strict=True)],
        "hint": hint(reach["minutes"], by_stop),
        "line": geo.reach_line(reach["settings"]),
    }


def hint(minutes: int, by_stop: dict[int, int]) -> str:
    later = [stop for stop in STOPS if stop > minutes]
    if not later:
        return "Dit is de ruimste stand."
    gain = by_stop[later[0]] - by_stop[minutes]
    if gain <= 0:
        return f"Met {later[0] - minutes} minuten meer komen er geen vacatures bij."
    return f"Met {later[0] - minutes} minuten meer {'komt er 1 vacature' if gain == 1 else f'komen er {gain} vacatures'} bij."
