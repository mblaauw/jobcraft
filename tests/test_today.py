"""The overview: what to do today, most urgent first, and the week in numbers."""

from __future__ import annotations

from datetime import date

from test_jobs_page import client  # noqa: F401

from app import today

TODAY = date(2026, 11, 3)


def card(job_id, status_since=None, due_days=None, cv=False, letter=False, days_here=None, company="Werkgever"):
    due = None
    if due_days is not None:
        moment = date.fromordinal(TODAY.toordinal() + due_days)
        due = {"date": moment.isoformat(), "days": due_days}
    return {
        "job": {"id": job_id, "title": f"Functie {job_id}", "company": company},
        "documents": {"cv": cv, "letter": letter},
        "due": due,
        "days_here": days_here,
        "since": status_since or {},
    }


def columns(**cards):
    return [{"status": status, "cards": cards.get(status, [])} for status in ("saved", "applied", "interview", "offer", "rejected")]


def test_an_interview_comes_before_a_deadline_and_a_deadline_before_the_rest(monkeypatch):
    monkeypatch.setattr(today.profile, "pending_draft", lambda: None)
    found = today.tasks(columns(
        saved=[card(1), card(2, due_days=1, cv=True, letter=True), card(3, due_days=-2)],
        applied=[card(4, days_here=9, company="DEPT"), card(5, days_here=2)],
        interview=[card(6, company="Gemeente")],
    ), TODAY)
    assert [task["title"] for task in found] == [
        "Bereid je gesprek bij Gemeente voor",
        "Verstuur je sollicitatie voor Functie 2",
        "Neem contact op met DEPT",
        "Maak je sollicitatie voor Functie 1",
    ]
    assert found[1]["tag"] == "Reageer voor morgen" and found[1]["urgent"]


def test_never_more_than_five_things(monkeypatch):
    monkeypatch.setattr(today.profile, "pending_draft", lambda: None)
    assert len(today.tasks(columns(saved=[card(n) for n in range(9)]), TODAY)) == 5


def test_dates_read_like_people_say_them():
    assert today.when(0, TODAY) == "vandaag"
    assert today.when(1, TODAY) == "morgen"
    assert today.when(3, date(2026, 11, 6)) == "vr 6 nov"


def test_the_week_counts_what_went_out_since_monday():
    week = today.week(columns(
        applied=[card(1, status_since={"applied": "2026-11-02"}), card(2, status_since={"applied": "2026-10-20"})],
        interview=[card(3, status_since={"applied": "2026-11-03"})],
    ), TODAY)
    assert (week["sent_week"], week["waiting"], week["talks"]) == (2, 2, 1)


def test_the_summary_says_what_today_is_about():
    assert today.summary([], 0).startswith("Niets dringends")
    assert today.summary([{}], 1) == "Vandaag: één ding om te doen en één nieuwe vacature die past."
    assert today.summary([{}, {}], 4) == "Vandaag: 2 dingen om te doen en 4 nieuwe vacatures die passen."


def test_the_overview_triages_in_place(client, monkeypatch):  # noqa: F811
    from app import profile, store
    from tests.test_jobs_page import add_job

    monkeypatch.setattr(profile, "profile_exists", lambda: True)
    job_id = add_job("Hovenier")
    page = client.get("/").text
    assert "Nieuw en kansrijk" in page and f'action="/jobs/{job_id}/status"' in page
    saved = client.post(f"/jobs/{job_id}/status", data={"status": "saved", "next": "/"}, follow_redirects=False)
    assert saved.headers["location"] == "/"
    assert "Maak je sollicitatie voor Hovenier" in client.get("/").text
    assert store.meta_get(today.VISIT_KEY)


def test_new_matches_skip_far_jobs_and_count_only_what_arrived_since_the_visit(client):  # noqa: F811
    import json

    from app import store
    from tests.test_jobs_page import add_job

    best, far, other = add_job("Hovenier", rank=90), add_job("Boomverzorger", rank=95), add_job("Tuinman", rank=70)
    store.set_job_travel(far, "test", json.dumps({"in_range": False}))
    picks, fresh = today.new_matches(1, "2000-01-01")
    assert [item["id"] for item in picks] == [best] and picks[0]["is_new"] and fresh == 2
    assert today.new_matches(5, "2999-01-01")[1] == 0
    picks, fresh = today.new_matches(5, None)
    assert [item["id"] for item in picks] == [best, other] and fresh == 0
