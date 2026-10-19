from __future__ import annotations

import json

import pytest

from app.profile import Preferences, parse_consolidation


def valid_payload():
    return {
        "profile_md": "# Profiel\n\nKern.",
        "preferences": {
            "updated_at": "2026-10-17",
            "tracks": [{"id": "groen", "name": "Groen", "weight": 0.6, "keywords": ["groen"]}],
            "likes": [{"tag": "buiten", "weight": 2, "evidence": "interview"}],
            "dislikes": [{"tag": "bellen", "weight": -3, "evidence": "interview"}],
            "hard_filters": {
                "location": "Amsterdam",
                "hours_min": 24,
                "hours_max": 32,
                "no_phone_main": True,
                "no_structural_evenings": True,
            },
            "keywords": {"boost": ["planten"], "veto": ["callcenter"]},
            "notes": "",
        },
    }


def test_parse_plain_json():
    data = parse_consolidation(json.dumps(valid_payload()))
    assert data["profile_md"].startswith("# Profiel")
    assert Preferences.model_validate(data["preferences"]).tracks[0].id == "groen"


def test_parse_with_markdown_fence_and_chatter():
    fenced = "Hier is het resultaat:\n```json\n" + json.dumps(valid_payload()) + "\n```\nKlaar."
    assert parse_consolidation(fenced)["preferences"]["keywords"]["veto"] == ["callcenter"]


def test_parse_accepts_fractional_weights():
    payload = valid_payload()
    payload["preferences"]["likes"][0]["weight"] = 1.5
    payload["preferences"]["dislikes"][0]["weight"] = -1.5
    data = parse_consolidation(json.dumps(payload))
    assert data["preferences"]["likes"][0]["weight"] == 1.5


def test_parse_rejects_missing_fields():
    with pytest.raises(ValueError):
        parse_consolidation('{"profile_md": "x"}')
