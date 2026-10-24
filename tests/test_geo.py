from __future__ import annotations

import pytest

from app import geo, profile, ranking, tenancy
from tests.factories import add_job


@pytest.fixture(autouse=True)
def known_home():
    profile.write_preferences({"home": {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041}})


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
    profile.write_preferences(
        {"home": {"label": "Utrecht", "lat": 52.0907, "lon": 5.1214, "bike_minutes": 10}}
    )
    geo._settings_cached.cache_clear()
    result = geo.assess("Utrecht")
    assert result["distance_km"] == 4.0
    assert result["in_range"] is True
    # ~40 km away from the custom home, over a 10-minute bike budget
    far = geo.assess("Amsterdam")
    assert far["in_range"] is False
    geo._settings_cached.cache_clear()


def test_place_matching_uses_word_boundaries():
    assert geo._find_place("centrum van laren") == "laren"
    assert geo._find_place("klarenbeek") != "laren"


def test_gazetteer_csv_is_loaded():
    assert geo.GAZETTEER_FILE.exists()
    assert "utrecht" in geo.PLACES


def test_two_accounts_have_independent_home_and_travel_cache():
    job_id = add_job(location="Eindhoven")
    from app import store

    with tenancy.scope({"id": "a", "username": "a"}):
        profile.write_preferences({"home": {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041}})
        assert ranking.travel_of(store.get_job(job_id))["in_range"] is False
    with tenancy.scope({"id": "b", "username": "b"}):
        profile.write_preferences({"home": {"label": "Eindhoven", "lat": 51.4416, "lon": 5.4697}})
        assert ranking.travel_of(store.get_job(job_id))["in_range"] is True
        profile.write_preferences({"home": {"label": "Eindhoven", "lat": 53.2194, "lon": 6.5665}})
        assert ranking.travel_of(store.get_job(job_id))["in_range"] is False


def test_precise_coordinates_do_not_get_synthetic_city_distance():
    result = geo.assess("Amsterdam", 52.3676, 4.9041)
    assert result["distance_km"] == 0


def test_missing_home_is_neutral_and_car_does_not_bypass_budget():
    profile.write_preferences({})
    assert geo.assess("Eindhoven")["in_range"] is None
    assert "woonplaats" in geo.assess("Eindhoven")["label"]
    profile.write_preferences(
        {"home": {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041, "car_available": True}}
    )
    assert geo.home()["car_available"] is True
    assert geo.assess("Eindhoven")["auto"] is False
    assert geo.assess("Eindhoven")["in_range"] is False


def test_gazetteer_is_authoritative_and_district_requires_city(tmp_path):
    assert len(geo.PLACES) == 68
    assert geo.load_gazetteer(tmp_path / "missing.csv") == ({}, set(), {})
    assert geo._find_place("centrum") is None
    assert geo._find_place("Amsterdam centrum") == "amsterdam"
    invalid = tmp_path / "bad.csv"
    invalid.write_text("name,lat,lon,rail,city\nbad,nan,5,0,\n")
    with pytest.raises(ValueError):
        geo.load_gazetteer(invalid)


@pytest.mark.parametrize(
    "description",
    [
        "Je bent in het bezit van rijbewijs B.",
        "Eigen vervoer vereist.",
        "Geen bedrijfsauto beschikbaar.",
    ],
)
def test_license_private_car_or_negated_company_car_does_not_bypass_range(description):
    result = geo.assess("Eindhoven", description=description)
    assert result["in_range"] is False and result["auto"] is False


@pytest.mark.parametrize(
    "description",
    [
        "Geen ervaring vereist\nBedrijfsauto beschikbaar",
        "Geen ervaring vereist, je krijgt een bedrijfsauto.",
    ],
)
def test_unrelated_negation_does_not_hide_available_employer_transport(description):
    assert geo.assess("Eindhoven", description=description)["auto"] is True


@pytest.mark.parametrize(
    "description",
    [
        "Een bedrijfsauto is helaas niet beschikbaar.",
        "Een bedrijfsauto is momenteel helaas niet meer beschikbaar.",
    ],
)
def test_modified_negative_availability_does_not_create_exemption(description):
    assert geo.assess("Eindhoven", description=description)["auto"] is False


def test_positive_availability_is_not_negated_by_later_experience_clause():
    assert (
        geo.assess(
            "Eindhoven", description="Een bedrijfsauto is beschikbaar, ervaring is niet vereist."
        )["auto"]
        is True
    )
