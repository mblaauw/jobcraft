from __future__ import annotations

from app import geo


def test_amsterdam_is_within_range():
    result = geo.assess("Amsterdam")
    assert result["in_range"] is True
    assert result["bike_min"] is not None


def test_rail_place_counts_as_within_range():
    zaandam = geo.assess("Zaandam")
    assert zaandam["in_range"] is True


def test_far_place_is_out_of_range():
    utrecht = geo.assess("Utrecht")
    assert utrecht["in_range"] is False
    assert utrecht["auto"] is False


def test_far_place_with_company_car_is_flagged_but_auto():
    result = geo.assess("Utrecht", description="Je rijdt langs locaties met een bedrijfsauto.")
    assert result["in_range"] is False
    assert result["auto"] is True


def test_coordinates_are_used_when_present():
    # Amsterdam centre coordinates -> in range, no gazetteer needed.
    near = geo.assess("", lat=52.3702, lon=4.8952)
    assert near["in_range"] is True
    # Groningen -> far.
    far = geo.assess("", lat=53.2194, lon=6.5665)
    assert far["in_range"] is False


def test_unknown_location_never_vetoes():
    result = geo.assess("")
    assert result["in_range"] is None
    assert result["distance_km"] is None


def test_city_in_description_does_not_place_the_job():
    result = geo.assess("", description="Ons kantoor in Groningen zoekt collega's")
    assert result["in_range"] is None


def test_amsterdam_uses_representative_in_city_distance():
    result = geo.assess("Amsterdam")
    assert result["in_range"] is True
    assert result["distance_km"] == 4.0
    assert "in Amsterdam" in result["label"]


def test_home_base_and_budget_come_from_preferences(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path)
    (tmp_path / "preferences.json").write_text(
        '{"home": {"label": "Utrecht", "lat": 52.0907, "lon": 5.1214, "bike_minutes": 10}}'
    )
    geo._settings_cached.cache_clear()
    result = geo.assess("Utrecht")
    assert result["distance_km"] == 0.0
    assert result["in_range"] is True
    # ~40 km away from the custom home, over a 10-minute bike budget
    far = geo.assess("Amsterdam")
    assert far["in_range"] is False
    geo._settings_cached.cache_clear()


def test_place_matching_uses_word_boundaries():
    assert geo._find_place("centrum van laren") == "laren"
    assert geo._find_place("klarenbeek") != "laren"
