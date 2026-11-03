"""Wat ik zoek: the choices that steer matching, in one place on the profile."""

from __future__ import annotations

from test_jobs_page import client  # noqa: F401

from app import onboarding, profile, store


def _prefs_with_learned_words():
    preferences = profile.read_preferences()
    preferences["likes"] = [{"tag": "buiten", "weight": 2.0}]
    preferences["dislikes"] = [{"tag": "callcenter", "weight": -2.0}]
    preferences["keywords"]["boost"] = ["groen"]
    preferences["keywords"]["veto"] = ["nachtdienst"]
    profile.write_preferences(preferences)


def test_the_tab_shows_learned_and_typed_words_together(client):  # noqa: F811
    _prefs_with_learned_words()
    page = client.get("/profile?tab=zoek").text
    assert 'action="/profile/zoek"' in page and 'action="/profile/home"' in page
    assert 'value="buiten, groen"' in page and 'value="callcenter, nachtdienst"' in page


def test_saving_keeps_learned_weights_and_ranks_again(client):  # noqa: F811
    _prefs_with_learned_words()
    answer = client.post("/profile/zoek", data={
        "roles": "Hovenier, Parkwachter", "hours": "24-32", "remote": "hybrid", "salary": "3000",
        "contract": ["vast"], "likes": "buiten, natuur", "veto": "nachtdienst, zwaar", "no_phone_main": "1",
    }, follow_redirects=False)
    assert answer.headers["location"] == "/profile?tab=zoek&saved=zoek"
    preferences = profile.read_preferences()
    assert [track["name"] for track in preferences["tracks"]] == ["Hovenier", "Parkwachter"]
    assert [(item["tag"], item["weight"]) for item in preferences["likes"]] == [("buiten", 2.0)]
    assert preferences["keywords"]["boost"] == ["natuur"]
    assert preferences["dislikes"] == [] and preferences["keywords"]["veto"] == ["nachtdienst", "zwaar"]
    assert (preferences["hard_filters"]["hours_min"], preferences["hard_filters"]["hours_max"]) == (24, 32)
    assert preferences["hard_filters"]["no_phone_main"] and preferences["wishes"]["salary_min"] == 3000
    with store.connect() as con:
        assert [row["kind"] for row in con.execute("SELECT kind FROM tasks")] == ["match"]


def test_the_quick_start_never_drops_learned_dislikes(client):  # noqa: F811
    _prefs_with_learned_words()
    preferences = profile.read_preferences()

    class Form(dict):
        def getlist(self, key):
            return []

    onboarding.apply_choices(preferences, Form(roles="", veto="zwaar"))
    assert [(item["tag"], item["weight"]) for item in preferences["dislikes"]] == [("callcenter", -2.0)]
    assert preferences["keywords"]["veto"] == ["zwaar"]


def test_own_words_are_edited_here_too(client):  # noqa: F811
    profile.write_profile("## Zoekprofiel\nGroen en buiten.\n\n## Kern\nFeit.\n", "test")
    page = client.get("/profile?tab=zoek").text
    assert "In je eigen woorden" in page and "Groen en buiten." in page and "Feit." not in page
    answer = client.post("/profile/sectie", data={"heading": "Zoekprofiel", "text": "Groen, buiten en rustig.", "terug": "zoek"}, follow_redirects=False)
    assert answer.headers["location"] == "/profile?tab=zoek&saved=woorden#woorden"
    assert "Groen, buiten en rustig." in profile.read_profile()


def test_the_advanced_tab_shows_only_what_lives_nowhere_else(client):  # noqa: F811
    profile.write_profile("## Kern\nKernfeit.\n\n## Zoekprofiel\nGroen.\n\n## Werkervaring\n### Rol\n- Punt\n", "test")
    from bs4 import BeautifulSoup

    page = client.get("/profile?tab=meer").text
    cards = [card.select_one(".profile-card-title").get_text(strip=True) for card in BeautifulSoup(page, "html.parser").select("#overzicht .profile-card")]
    assert "Geavanceerd" in page and cards == ["Kern"]
    assert "Wat telt bij matching" not in page and 'id="reisbereik"' not in page
