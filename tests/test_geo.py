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
    assert len(geo.PLACES) > 2500
    assert geo.PLACE_LABELS["nijkerk"] == "Nijkerk"
    assert geo.load_gazetteer(tmp_path / "missing.csv") == ({}, set(), {}, {})
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


@pytest.mark.parametrize(
    "text, place",
    [
        ("Het Scheepvaartmuseum in Amsterdam zoekt een Onderzoeker voor 16 uur per week", "Amsterdam"),
        ("ELIA in Amsterdam is looking for an Events Intern", "Amsterdam"),
        ("Design Museum Den Bosch in \u2018s-Hertogenbosch zoekt een Directie-assistent", "'s-Hertogenbosch"),
        ("Functie\nStandplaats: Dordrecht\nUren: 16", "Dordrecht"),
        ("Recruiter | Waterschap Scheldestromen Locatie Middelburg Opleiding MBO", "Middelburg"),
        ("Fondsenwerver Vogelbescherming Nederland · Zeist Parttime Tijdelijk 32 uur", "Zeist"),
        ("Projectcoördinator (32 uur)\nStichting Kunstwerk Oost, Amsterdam-Oost\n\nWie zijn wij", "Amsterdam"),
        # Places that are only mentioned, not where the work is:
        ("Wij werken voor klanten in heel Nederland, onder meer in Utrecht en Den Haag.", ""),
        ("Doorgaan naar inhoud 10 november, Fokker Terminal Den Haag Bestel je tickets", ""),
        ("De workshop vindt plaats op dinsdag 1 december in Amsterdam", ""),
        ("Locatie thuis of op kantoor", ""),
    ],
)
def test_a_vacancy_states_its_own_place(text, place):
    assert geo.place_in_text(text).lower() == place.lower()


def test_a_board_without_a_place_field_still_gets_one_from_the_text():
    from app.adapters.common import make_job

    job = make_job("test", "https://x.test/1", "Bedrijfsleider", description="Het Openluchttheater in Haarlem zoekt een Bedrijfsleider")
    assert job["location"] == "Haarlem"
    assert make_job("test", "https://x.test/2", "Gids", location="Leiden", description="X in Haarlem zoekt")["location"] == "Leiden"


def test_stored_vacancies_without_a_place_get_one_once():
    from app import store
    from app.store import schema

    stated = add_job(1, location="", description="Museum X in Utrecht zoekt een gids voor 24 uur")
    silent = add_job(2, location="", description="Wij zoeken een gids.")
    with store.connect() as con:
        con.execute("DELETE FROM meta WHERE key='places_from_text_v1'")
        schema._fill_places_from_text(con)
    assert store.get_job(stated)["location"] == "Utrecht"
    assert not store.get_job(silent)["location"]


def test_fully_remote_work_is_never_too_far():
    far = geo.assess("Groningen", description="Deze functie is volledig remote.")
    assert far["in_range"] is False and far["remote"] is True and far["too_far"] is False
    assert far["label"].startswith("volledig thuiswerk")
    placeless = geo.assess("Remote - Netherlands")
    assert placeless["remote"] is True and placeless["label"] == "volledig thuiswerk"
    assert geo.assess("Groningen", description="Hybride: deels thuis.")["too_far"] is True


def test_a_company_car_job_stays_in_the_list_and_in_matching():
    from app import store

    car = add_job(1, location="Eindhoven", description="Je rijdt naar klanten met een bedrijfsauto.")
    far = add_job(2, location="Eindhoven", description="Je werkt op ons kantoor.")
    remote = add_job(3, location="Groningen", description="Volledig remote, door heel Nederland.")
    ranking.refresh_travel()
    listed = {row["id"] for row in store.list_jobs_page(10, 0, within_travel=True)}
    assert car in listed and remote in listed and far not in listed
    assert not ranking.excluded_from_matching(store.get_job(car))
    assert ranking.excluded_from_matching(store.get_job(far))


def test_your_own_car_counts_with_its_own_minutes():
    profile.write_preferences(
        {"home": {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041, "transit_minutes": 35, "car_minutes": 50}}
    )
    utrecht = geo.assess("Utrecht")
    assert utrecht["in_range"] is True and "min auto" in utrecht["label"]
    assert geo.assess("Eindhoven")["in_range"] is False


def test_a_budget_of_zero_switches_that_way_of_travelling_off():
    profile.write_preferences(
        {"home": {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041, "bike_minutes": 0, "transit_minutes": 60}}
    )
    assert geo.assess("Amsterdam")["in_range"] is True  # by OV
    profile.write_preferences(
        {"home": {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041, "bike_minutes": 0, "transit_minutes": 0}}
    )
    assert geo.assess("Amsterdam")["in_range"] is False


def test_an_e_bike_goes_further_than_a_bike():
    settings = geo.home()
    ebike = geo.assess("Haarlem", home_settings=settings | {"bike_kind": "ebike"})
    bike = geo.assess("Haarlem", home_settings=settings | {"bike_kind": "fiets"})
    assert bike["bike_min"] > ebike["bike_min"]
    assert "e-bike" in ebike["label"] and "min fiets" in bike["label"]


def test_an_older_car_setting_keeps_meaning_as_far_as_by_ov():
    from app.preference_models import HomeBase

    old = HomeBase.model_validate({"car_available": True, "transit_minutes": 45})
    assert old.car_minutes == 45 and old.car_available is True
    assert HomeBase.model_validate({"car_minutes": 0, "car_available": True}).car_available is False


@pytest.mark.parametrize(
    "text, place",
    [("Den Bosch", "'s-Hertogenbosch"), ("Bergen, NH", "Bergen (NH)"), ("Hoogvliet", "Hoogvliet Rotterdam"), ("Zaanstad", "Zaandam")],
)
def test_names_boards_use_for_a_catalogue_place(text, place):
    assert geo.assess(text)["place"] == place


def test_starting_out_one_budget_goes_to_each_way_you_travel():
    from app import onboarding

    assert onboarding.save_home("Utrecht", 45, ["fiets", "auto"])
    home = profile.read_preferences()["home"]
    assert (home["bike_minutes"], home["bike_kind"], home["transit_minutes"], home["car_minutes"]) == (45, "fiets", 0, 45)
    assert onboarding.current_modes(home) == ["fiets", "auto"]
    assert onboarding.save_home("Utrecht", 30, [])  # nothing ticked: e-bike and OV
    home = profile.read_preferences()["home"]
    assert (home["bike_kind"], home["bike_minutes"], home["transit_minutes"], home["car_minutes"]) == ("ebike", 30, 30, 0)


def test_the_reach_reads_as_you_set_it():
    assert geo.reach_line({"bike_minutes": 45, "transit_minutes": 45}) == "45 min e-bike of 45 min OV"
    assert geo.reach_line({"bike_minutes": 0, "transit_minutes": 0, "car_minutes": 30}) == "30 min auto"
    assert geo.reach_line({"bike_minutes": 0, "transit_minutes": 0}) == "geen manier van reizen gekozen"
