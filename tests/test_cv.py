"""Mijn cv: reading a CV in, editing it part by part, and the coach's notes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_jobs_page import client  # noqa: F401
from test_studio import _manager

from app import application_plan, cv_ai, cv_model, profile, runtime, store, tasks

READ = {
    "summary": "Adviseur die klanten en monteurs bij elkaar brengt.",
    "roles": [
        {"title": "Adviseur", "organisation": "Voorbeeldbedrijf", "place": "Utrecht", "period": "jun 2024 – heden",
         "points": ["Stelt offertes op na een inschatting op foto's.", "Plant afspraken tussen klant en monteur."]},
        {"title": "Gids", "organisation": "Rondvaart BV", "place": "Amsterdam", "period": "2023 – 2024", "points": ["Gaf rondvaarten."]},
    ],
    "education": [{"title": "Bachelor Business", "school": "Hogeschool", "period": "2013 – 2017"}],
    "skills": ["Klantcontact", "Planning"],
    "languages": ["Nederlands — moedertaal"],
    "extras": ["Rijbewijs B"],
    "interests": [],
}


@pytest.fixture()
def cv(client):  # noqa: F811
    profile.write_profile("# Profiel\n\n## Zoekprofiel\nBuiten en mensen.\n\n## Privé\nGeheim.\n", "test")
    data = cv_model.from_read(READ)
    cv_model.save(data, note="test")
    return cv_model.load()


def _run(kind):
    """What the worker does with the queued CV task."""
    while task := tasks.claim_next():
        if task["kind"] == kind:
            (cv_ai._task_import if kind == "cv_import" else cv_ai._task_coach)(task)
        tasks.finish(task["id"])


def test_the_cv_becomes_profile_text_and_leaves_the_rest_alone(cv):
    text = profile.read_profile()
    assert "### jun 2024 – heden — Adviseur | Voorbeeldbedrijf, Utrecht\n- Stelt offertes op" in text
    assert "## Zoekprofiel\nBuiten en mensen." in text and "## Privé\nGeheim." in text
    assert "- Bachelor Business — Hogeschool, 2013 – 2017" in text
    assert all(point["source"] == "cv" for role in cv["roles"] for point in role["points"])


def test_edited_lines_keep_where_they_came_from(cv):
    points = cv["roles"][0]["points"]
    kept = cv_model.points_from_lines(points, f"{points[0]['text']}\nNieuw punt van mij")
    assert kept[0]["id"] == points[0]["id"] and kept[0]["source"] == "cv"
    assert kept[1]["source"] == "eigen"


def test_reading_in_strips_contact_details_and_waits_for_approval(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(runtime, "manager", _manager([READ, {"notes": []}]))
    pasted = "Jan Voorbeeld\njan@voorbeeld.nl\n06 12345678\n\n" + "Adviseur bij Voorbeeldbedrijf. " * 6
    client.post("/profile/cv/inlezen", data={"bron": "tekst", "tekst": pasted})
    waiting = json.loads(store.meta_get(cv_ai.TEXT_KEY))
    assert "jan@voorbeeld.nl" not in waiting["text"] and "12345678" not in waiting["text"]
    assert "We lezen je cv" in client.get("/profile").text
    _run("cv_import")
    assert store.meta_get(cv_ai.TEXT_KEY) == "{}" and cv_model.load() is None
    page = client.get("/profile").text
    assert "Zo hebben we je cv gelezen" in page and "Voorbeeldbedrijf" in page
    client.post("/profile/cv/omzetting", data={"wat": "overnemen"})
    assert cv_model.load()["roles"][0]["organisation"] == "Voorbeeldbedrijf"
    assert cv_ai.status()["kind"] == "coach"  # the coach starts reading straight away


def test_the_coach_only_keeps_notes_it_can_place_and_support(cv):
    point = cv["roles"][0]["points"][0]
    answer = {"notes": [
        {"target": cv["roles"][0]["id"], "kind": "vraag", "text": "Hoeveel offertes per week?", "suggestion": ""},
        {"target": point["id"], "kind": "sterker", "text": "Actiever.", "suggestion": "Maakt offertes op basis van foto's."},
        {"target": point["id"], "kind": "sterker", "text": "Met getal.", "suggestion": "Maakte 40 offertes per week."},
        {"target": "bestaat-niet", "kind": "vraag", "text": "Weg", "suggestion": ""},
        {"target": "summary", "kind": "onzin", "text": "Weg", "suggestion": ""},
    ]}
    found = cv_ai.coach(_manager([answer]), cv)
    assert [note["text"] for note in found] == ["Hoeveel offertes per week?", "Actiever."]
    cv_ai.save_notes(found, cv_model.digest(cv))
    view = cv_ai.view(cv)
    assert view["open"] == 2 and len(view["by_place"][cv["roles"][0]["id"]]) == 2 and not view["stale"]


def test_each_coach_action_does_what_it_says(client, cv):  # noqa: F811
    role, point, other = cv["roles"][0], cv["roles"][0]["points"][0], cv["roles"][1]["points"][0]
    notes = [
        {"id": "n1", "target": role["id"], "kind": "vraag", "text": "Hoeveel offertes?", "suggestion": "", "state": "open"},
        {"id": "n2", "target": point["id"], "kind": "sterker", "text": "Actiever.", "suggestion": "Maakt offertes op basis van foto's.", "state": "open"},
        {"id": "n3", "target": other["id"], "kind": "niet_claimen", "text": "Te stellig.", "suggestion": "", "state": "open"},
        {"id": "n4", "target": "summary", "kind": "klopt_niet", "text": "Twee jaartallen.", "suggestion": "", "state": "open"},
    ]
    cv_ai.save_notes(notes, cv_model.digest(cv))
    page = client.get("/profile").text
    assert "Vraag van de coach" in page and "Sterker te zeggen" in page and 'action="/profile/cv/coach/n4"' in page
    assert client.post("/profile/cv/coach/n1", data={"actie": "antwoord", "antwoord": ""}, follow_redirects=False).status_code == 303
    client.post("/profile/cv/coach/n1", data={"actie": "antwoord", "antwoord": "Ongeveer 25 per week"})
    client.post("/profile/cv/coach/n2", data={"actie": "overnemen"})
    client.post("/profile/cv/coach/n3", data={"actie": "weghalen"})
    client.post("/profile/cv/coach/n4", data={"actie": "laat"})
    data = cv_model.load()
    first = data["roles"][0]["points"]
    assert first[0] == {"id": point["id"], "text": "Maakt offertes op basis van foto's.", "source": "coach"}
    assert first[-1]["text"] == "Ongeveer 25 per week" and first[-1]["source"] == "antwoord"
    assert data["roles"][1]["points"] == []
    assert application_plan.stories()[-1]["answer"] == "Ongeveer 25 per week"
    assert [note["state"] for note in cv_ai.notes()["notes"]] == ["done", "done", "done", "dismissed"]
    assert not cv_ai.view(data)["stale"]


def test_editing_parts_saves_and_reorders(client, cv):  # noqa: F811
    first, second = cv["roles"][0]["id"], cv["roles"][1]["id"]
    client.post("/profile/cv/over-mij", data={"tekst": "Nieuwe samenvatting."})
    client.post("/profile/cv/functie", data={"id": first, "functie": "Senior adviseur", "punten": "Een\nTwee"})
    client.post("/profile/cv/functie", data={"functie": "Stagiair", "organisatie": "Stichting"})
    client.post(f"/profile/cv/functie/{second}/verwijderen")
    client.post("/profile/cv/opleiding", data={"tekst": "Master | Universiteit | 2018"})
    client.post("/profile/cv/lijst/skills", data={"tekst": "- Plannen\n- Adviseren\n"})
    data = cv_model.load()
    assert data["summary"] == "Nieuwe samenvatting."
    assert [role["title"] for role in data["roles"]] == ["Stagiair", "Senior adviseur"]
    client.post(f"/profile/cv/functie/{first}/omhoog")
    assert cv_model.load()["roles"][0]["id"] == first
    assert data["education"][0]["school"] == "Universiteit" and data["skills"] == ["Plannen", "Adviseren"]
    assert "## Vaardigheden\n- Plannen\n- Adviseren" in profile.read_profile()
    assert client.post("/profile/cv/lijst/geheim", data={"tekst": "x"}).status_code == 404


def test_without_a_cv_the_page_invites_one_and_tabs_still_work(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(runtime, "manager", SimpleNamespace(client=None))
    page = client.get("/profile").text
    assert "Zet je cv hier neer" in page and 'action="/profile/cv/inlezen"' in page
    assert "Woonplaats en reisbereik" in client.get("/profile?tab=zoek").text
    assert client.post("/profile/cv/over-mij", data={"tekst": "x"}).status_code == 409


def test_an_id_with_digits_never_vouches_for_a_number(cv):
    cv["roles"][0]["points"][0]["id"] = "p3f40e2"  # ids are random; this one contains "40"
    point = cv["roles"][0]["points"][0]["id"]
    answer = {"notes": [{"target": point, "kind": "sterker", "text": "Met getal.", "suggestion": "Maakte 40 offertes per week."}]}
    assert cv_ai.coach(_manager([answer]), cv) == []
