"""preference models primitives."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .util import now_iso


class Track(BaseModel):
    id: str
    name: str
    weight: float = 1.0
    keywords: list[str] = Field(default_factory=list)


class PreferenceItem(BaseModel):
    tag: str
    weight: float = 1.0
    evidence: str = ""


class HardFilters(BaseModel):
    location: str = ""
    hours_min: float | None = None
    hours_max: float | None = None
    no_phone_main: bool = False
    no_structural_evenings: bool = False
    no_heavy_physical: bool = False
    day_work_only: bool = False
    wants_guidance: bool = False


class Keywords(BaseModel):
    boost: list[str] = Field(default_factory=list)
    veto: list[str] = Field(default_factory=list)


class HomeBase(BaseModel):
    """Home point for travel-time estimates (edit in preferences.json)."""

    label: str = "Nog niet ingesteld"
    lat: float = Field(default=0, ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(default=0, ge=-180, le=180, allow_inf_nan=False)
    bike_minutes: int = 30
    transit_minutes: int = 35
    car_available: bool = False


class Preferences(BaseModel):
    updated_at: str = Field(default_factory=now_iso)
    tracks: list[Track] = Field(default_factory=list)
    likes: list[PreferenceItem] = Field(default_factory=list)
    dislikes: list[PreferenceItem] = Field(default_factory=list)
    hard_filters: HardFilters = Field(default_factory=HardFilters)
    keywords: Keywords = Field(default_factory=Keywords)
    home: HomeBase = Field(default_factory=HomeBase)
    notes: str = ""

    @field_validator("tracks")
    @classmethod
    def unique_tracks(cls, tracks: list[Track]) -> list[Track]:
        ids = [track.id for track in tracks]
        if len(set(ids)) != len(ids) or "overig" in ids or any(not item.strip() for item in ids):
            raise ValueError("track ids must be unique, nonempty and not 'overig'")
        return tracks


def public_preferences(preferences: dict) -> dict:
    """Only matching wishes, never free-form notes/evidence or home coordinates."""
    return {
        "tracks": [
            {key: item[key] for key in ("id", "name", "weight", "keywords") if key in item}
            for item in preferences.get("tracks", [])
        ],
        "likes": [
            {key: item[key] for key in ("tag", "weight") if key in item}
            for item in preferences.get("likes", [])
        ],
        "dislikes": [
            {key: item[key] for key in ("tag", "weight") if key in item}
            for item in preferences.get("dislikes", [])
        ],
        "hard_filters": {
            key: value
            for key, value in preferences.get("hard_filters", {}).items()
            if key
            in (
                "hours_min",
                "hours_max",
                "no_phone_main",
                "no_structural_evenings",
                "no_heavy_physical",
                "day_work_only",
                "wants_guidance",
            )
        },
        "keywords": {
            key: value
            for key, value in (preferences.get("keywords") or {}).items()
            if key in ("boost", "veto")
        },
    }


def _neutral_preferences(data: dict) -> dict:
    """Never inherit the legacy account's home or restrictive defaults."""
    return {
        **data,
        "hard_filters": {
            "no_phone_main": False,
            "no_structural_evenings": False,
            **(data.get("hard_filters") or {}),
        },
        "home": {"label": "Nog niet ingesteld", "lat": 0, "lon": 0, **(data.get("home") or {})},
    }


def _validate_preferences(data: dict) -> Preferences:
    return Preferences.model_validate(_neutral_preferences(data))
