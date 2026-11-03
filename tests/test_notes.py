"""Notities: private notes that never reach the AI, and versions that show what changed."""

from __future__ import annotations

from test_jobs_page import client  # noqa: F401

from app import application_plan, private_notes, profile, profile_versions


def test_notes_are_saved_apart_from_everything_the_ai_reads(client):  # noqa: F811
    profile.write_profile("## Kern\nOpenbaar feit.\n", "test")
    answer = client.post("/profile/notities", data={"tekst": "Geheim: diploma nakijken\r\n"}, follow_redirects=False)
    assert answer.headers["location"] == "/profile?tab=notities&saved=notes#notities"
    assert private_notes.load()["text"] == "Geheim: diploma nakijken"
    for text in (profile.read_profile(), profile.read_public_excerpt(20000), profile.read_document_excerpt(20000), application_plan.person_source()):
        assert "diploma nakijken" not in text
    page = client.get("/profile?tab=notities").text
    assert "Geheim: diploma nakijken" in page and "nooit naar de AI" in page


def test_each_version_shows_what_its_change_changed(client):  # noqa: F811
    profile.write_profile("## Kern\nEerste.\n", "test")
    profile.write_profile("## Kern\nTweede.\n", "eerste wijziging")
    profile.write_profile("## Kern\nTweede.\n\n## Extra\nNieuw stuk.\n", "tweede wijziging")
    newest, older = profile_versions.history()[:2]
    assert newest["note"] == "tweede wijziging" and ("add", "Nieuw stuk.") in newest["lines"] and newest["removed"] == 0
    assert older["note"] == "eerste wijziging" and ("del", "Eerste.") in older["lines"] and ("add", "Tweede.") in older["lines"]
    page = client.get("/profile?tab=notities").text
    assert "Zet de tekst van vóór deze wijziging terug" in page and "Nieuw stuk." in page
