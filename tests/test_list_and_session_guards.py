"""What someone is working on stays where it is, and nothing is reported as done that was not."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app import applications, notifications, store, tasks, today, workflows
from tests.test_jobs_page import add_job, client, soup  # noqa: F401


def test_an_application_stays_on_the_board_when_its_source_is_switched_off(client):  # noqa: F811
    store.sync_sources([{"id": "test", "kind": "rss"}])
    talking, fresh = add_job("Gesprek"), add_job("Nieuw")
    store.update_job_status(talking, "interview")
    store.set_user_source("test", False)
    cards = [card["job"]["id"] for column in applications.board() for card in column["cards"]]
    assert cards == [talking]
    assert [row["id"] for row in store.list_jobs_page(10, 0)] == [talking]  # the new one is hidden, as asked
    assert fresh not in cards


def test_a_list_row_cannot_take_an_interview_off_the_board(client):  # noqa: F811
    job_id = add_job("Gesprek")
    store.update_job_status(job_id, "interview")
    for status in ("ignored", "saved", "new"):
        response = client.post(f"/jobs/{job_id}/status", data={"status": status, "next": "/jobs"}, follow_redirects=False)
        assert response.status_code == 303
        assert store.get_job(job_id)["status"] == "interview"
    row = soup(client.get("/jobs?status=interview")).select_one(f"#job-{job_id}")
    assert all(button.has_attr("disabled") for button in row.select(".vacancy-save button"))
    # Sollicitaties itself still moves it, and a saved vacancy still becomes "gereageerd" from its page.
    saved = add_job("Bewaard")
    store.update_job_status(saved, "saved")
    client.post(f"/jobs/{saved}/status", data={"status": "applied"}, follow_redirects=False)
    assert store.get_job(saved)["status"] == "applied"


def test_new_stays_new_for_the_whole_sitting():
    long_ago = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    store.meta_set(today.VISIT_KEY, long_ago)
    assert today.last_visit() == long_ago  # the first view of this sitting
    assert today.last_visit() == long_ago  # a reload, or back from a vacancy
    store.meta_set(today.VISIT_KEY, (datetime.now(UTC) - timedelta(hours=3)).isoformat())
    assert today.last_visit() > long_ago  # a later sitting counts from the previous one


def test_the_new_matches_page_survives_a_reload(client):  # noqa: F811
    store.meta_set("matches_seen_at", "2020-01-01T00:00:00+00:00")
    add_job("Sterk", rank=90)
    notifications.refresh()
    assert "Sterk" in soup(client.get("/jobs/nieuw")).get_text()
    assert "Sterk" in soup(client.get("/jobs/nieuw")).get_text()
    assert not soup(client.get("/jobs")).select(".nav-badge")  # seen is seen: the badge is gone


def test_a_fresh_strong_match_is_found_behind_many_older_ones(client):  # noqa: F811
    for index in range(105):
        add_job(f"Oud {index}", rank=90)
    store.meta_set("matches_seen_at", datetime.now(UTC).isoformat())
    fresh = add_job("Vers", rank=72)
    assert [job["id"] for job in notifications._strong_new_jobs()] == [fresh]


def test_only_the_mailed_matches_count_as_alerted(client, monkeypatch):  # noqa: F811
    from app import digest

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_FROM", "jobs@test")
    monkeypatch.setattr(digest, "deliver", lambda message: None)
    monkeypatch.setattr(digest, "settings", lambda: {"enabled": True, "email": "sam@example.test"})
    jobs = [{"id": index, "rank_score": 95 - index, "title": f"Sterk {index}", "company": "", "location": "", "url": ""} for index in range(1, 6)]
    monkeypatch.setattr(digest, "compose", lambda picked, email: picked)
    notifications._alert_if_due(jobs)
    assert json.loads(store.meta_get("match_alerted")) == [1, 2, 3]


def test_hours_and_salary_filters_read_whole_words(client):  # noqa: F811
    def job(title, text, hours=""):
        job_id, _ = store.upsert_job({"source_id": "test", "url": f"https://example.test/{title}", "title": title,
                                      "company": "Testbedrijf", "description": text, "hours": hours, "content_hash": title})
        return job_id

    small = job("Klein", "een functie van 8 uur per week")
    big = job("Groot", "een functie van 38 uur per week", hours="38 uur")
    paid = job("Betaald", "salaris € 3.000 bruto")
    job("Monteur", "wij zoeken een monteur voor de werkplaats")

    def found(**filters):
        return {row["id"] for row in store.list_jobs_page(20, 0, list_filters=filters)}

    assert small in found(hours="tot24") and big not in found(hours="tot24")
    assert found(salaris="salaris") == {paid}


def test_an_expired_session_answers_a_script_plainly_and_a_form_with_a_real_page(client):  # noqa: F811
    job_id = add_job()
    client.cookies.clear()
    asked = client.post(f"/jobs/{job_id}/vote", data={"vote": "up"}, headers={"accept": "application/json"}, follow_redirects=False)
    assert asked.status_code == 401
    sent = client.post(f"/jobs/{job_id}/vote", data={"vote": "up"}, headers={"referer": "http://testserver/jobs?q=groen"}, follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"] == "/login?next=%2Fjobs%3Fq%3Dgroen"
    elsewhere = client.post(f"/jobs/{job_id}/vote", data={"vote": "up"}, headers={"referer": "https://evil.test/x"}, follow_redirects=False)
    assert elsewhere.headers["location"] == "/login"


def test_document_work_that_waits_is_not_called_lost_and_lost_work_is(client):  # noqa: F811
    from app import tenancy

    owner = tenancy.user_id() or (store.connect and None)
    with tenancy.scope({"id": "u1", "username": "u1", "display_name": "U", "role": "user"}):
        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        store.meta_set("doc_status:7", json.dumps({"state": "running", "kind": "resume", "started_at": old, "updated_at": old}))
        assert workflows._doc_status(7)["state"] == "error"  # nothing is queued for it
        tasks.enqueue("document", owner="u1", payload={"job_id": 7, "kind": "resume"}, dedupe_key="document:u1:7:resume")
        assert workflows._doc_status(7)["state"] == "running"  # behind other work, but coming
        store.meta_set("doc_status:8", json.dumps({"state": "running", "kind": "resume"}))
        assert workflows._doc_status(8)["state"] == "error"  # no moment at all: never started
    assert owner is None or owner
