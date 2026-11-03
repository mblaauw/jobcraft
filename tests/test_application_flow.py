"""From CV to a sendable application: contact details, the list, the checks and the quick start."""

from __future__ import annotations

import json
from types import SimpleNamespace

from test_jobs_page import add_job, client  # noqa: F401
from test_studio import CV, LETTER, PROFILE, _no_pdf, _plan

from app import (
    cv_ai,
    cv_model,
    cv_text,
    document_contact,
    document_pipeline,
    profile,
    runtime,
    store,
    today,
)

CV_TOP = "# CV Jan de Vries 2026 — voor Voorbeeld\n\nTelefoon: 06 12345678\njan@voorbeeld.nl | linkedin.com/in/janvries\n"


def test_contact_details_are_read_from_the_top_of_a_cv_and_can_be_taken_out():
    found = cv_text.contact_of(CV_TOP + "\n## Werkervaring\n### jan 2018 – okt 2020 — Planner\n")
    assert found == {"name": "Jan de Vries", "email": "jan@voorbeeld.nl", "linkedin": "linkedin.com/in/janvries", "phone": "06 12345678"}
    assert cv_text.strip_contact("Telefoon: 06 12345678\njan@voorbeeld.nl\nPlanner van 2016 - 2017") == "Planner van 2016 - 2017"


def test_reading_a_cv_suggests_contact_details_until_the_person_saves_their_own(client):  # noqa: F811
    cv_ai.source_text("tekst", CV_TOP + "\nAdviseur bij Voorbeeldbedrijf. " * 4)
    contact = document_contact.load()
    assert contact["name"] == "Jan de Vries" and contact["phone"] == "06 12345678"
    assert document_contact.from_cv_unconfirmed(contact) and not document_contact.missing(contact)
    document_contact.save({**contact, "phone": "06 99999999"})
    assert document_contact.load()["phone"] == "06 99999999" and not document_contact.from_cv_unconfirmed(document_contact.load())


def test_moving_contact_details_out_of_the_profile_keeps_them_for_documents(client):  # noqa: F811
    profile.write_profile(CV_TOP + "\n## Over mij\nPlanner. Bel 06 12345678.\n\n## Privé\nGeheim.\n", "test")
    cv_model.save(cv_model.from_read({"summary": "Planner. Mail jan@voorbeeld.nl", "roles": []}), note="test")
    page = client.get("/profile").text
    assert "Contactgegevens staan" in page and 'action="/profile/contact-apart"' in page
    client.post("/profile/contact-apart")
    text = profile.read_profile()
    assert "12345678" not in text and "jan@voorbeeld.nl" not in text and "## Privé\nGeheim." in text
    assert "jan@voorbeeld.nl" not in cv_model.load()["summary"]
    contact = document_contact.load()
    assert (contact["name"], contact["email"], contact["phone"]) == ("Jan de Vries", "jan@voorbeeld.nl", "06 12345678")


def test_a_vacancy_with_a_plan_is_on_the_list(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(runtime, "manager", SimpleNamespace(client=None))
    profile.write_profile(PROFILE, "test")
    planned, asked = add_job("Planner"), add_job("Adviseur")
    _plan(planned)
    client.get(f"/jobs/{planned}/sollicitatie")
    assert store.get_job(planned)["status"] == "saved"
    client.post(f"/jobs/{asked}/sollicitatie/plan")
    assert store.get_job(asked)["status"] == "saved"
    store.update_job_status(planned, "applied")
    client.get(f"/jobs/{planned}/sollicitatie")
    assert store.get_job(planned)["status"] == "applied"  # never moved back


def test_saving_contact_details_puts_them_on_a_new_version(client, monkeypatch):  # noqa: F811
    _no_pdf(monkeypatch)
    profile.write_profile(PROFILE, "test")
    job_id = add_job()
    _plan(job_id)
    first = document_pipeline.save_version(dict(store.get_job(job_id)), "resume", CV)
    page = client.get(f"/jobs/{job_id}/sollicitatie?tab=cv").text
    assert "Je naam of contactgegevens ontbreken" in page
    fields = {"name": "Jan de Vries", "email": "jan@voorbeeld.nl", "phone": "", "place": "", "linkedin": "", "tab": "cv"}
    answer = client.post(f"/jobs/{job_id}/sollicitatie/gegevens", data=fields, follow_redirects=False)
    assert "gegevens-nieuw" in answer.headers["location"]
    assert sorted(row["version"] for row in store.list_documents(job_id)) == [1, 2]
    assert store.get_document(first["id"])["version"] == 1  # the old one stays as it was
    client.post(f"/jobs/{job_id}/sollicitatie/gegevens", data=fields)
    assert len(store.list_documents(job_id)) == 2  # nothing changed, nothing made
    assert "Je naam of contactgegevens ontbreken" not in client.get(f"/jobs/{job_id}/sollicitatie?tab=cv").text
    store.meta_set(document_contact.SETTING, json.dumps({**fields, "phone": "06 12345678"}))
    assert "Je gegevens zijn veranderd sinds deze versie" in client.get(f"/jobs/{job_id}/sollicitatie?tab=cv").text
    client.post(f"/jobs/{job_id}/sollicitatie/gegevens/vernieuwen", data={"tab": "cv"})
    assert len(store.list_documents(job_id)) == 3
    assert "Je gegevens zijn veranderd sinds deze versie" not in client.get(f"/jobs/{job_id}/sollicitatie?tab=cv").text


def test_points_to_check_come_once_and_can_be_rewritten_away(client, monkeypatch):  # noqa: F811
    _no_pdf(monkeypatch)
    monkeypatch.setattr(runtime, "manager", SimpleNamespace(client=object()))
    review = {"warnings": ['De naam of term "Suppliers" staat niet in je gegevens.'],
              "unsupported": ["Je schrijft 'Suppliers', maar in je gegevens staat leveranciers.", "Je schrijft 'dagelijks'."]}
    assert document_pipeline.check_points(review) == [review["warnings"][0], review["unsupported"][1]]
    instruction = document_pipeline._instruction(document_pipeline.CHECKED, {"review": review})
    assert "Suppliers" in instruction and "dagelijks" in instruction
    profile.write_profile(PROFILE, "test")
    job_id = add_job()
    _plan(job_id)
    first = document_pipeline.save_version(dict(store.get_job(job_id)), "motivatie", LETTER, review=review)
    assert "Herschrijf zonder deze punten" in client.get(f"/jobs/{job_id}/sollicitatie?tab=brief").text
    client.post(f"/jobs/{job_id}/sollicitatie/documenten/{first['id']}/toon", data={"tone": "nagekeken"})
    assert json.loads(store.meta_get(f"doc_status:{job_id}"))["kind"] == f"tone:nagekeken:{first['id']}"


def test_with_only_the_questions_left_today_offers_them_instead_of_a_warning(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(today, "start_state", lambda: "questions")
    document_contact.save({"name": "Jan de Vries", "email": "jan@voorbeeld.nl"})
    monkeypatch.setattr(profile, "profile_exists", lambda: True)
    page = client.get("/").text
    assert "Hallo Jan" in page and "Drie korte vragen" in page and "snelle start is nog niet af" not in page


def test_a_username_saved_as_name_gives_way_to_the_name_on_the_cv(client):  # noqa: F811
    profile.write_profile(CV_TOP + "\n## Over mij\nPlanner.\n", "test")
    document_contact.save({"name": "jan", "email": "jan@voorbeeld.nl"})
    assert document_contact.load()["name"] == "Jan de Vries" and today.first_name() == "Jan"
