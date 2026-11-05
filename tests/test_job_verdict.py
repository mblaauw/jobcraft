"""Opening a vacancy without an assessment asks for one, and the page swaps it in."""

from __future__ import annotations

from types import SimpleNamespace

from test_jobs_page import add_job, client  # noqa: F401
from test_studio import _manager

from app import job_verdict, matching, runtime, store, tasks

VERDICT = {"score": 72, "why": "Veel klantcontact en planning, dat sluit aan.", "concerns": ["Ook avondwerk."], "questions_for_employer": []}


def _run() -> int:
    done = 0
    while task := tasks.claim_next():
        if task["kind"] == job_verdict.KIND:
            job_verdict._task(task)
            done += 1
        tasks.finish(task["id"])
    return done


def _ai(monkeypatch, reply):
    monkeypatch.setattr(runtime, "manager", _manager([reply]))
    monkeypatch.setattr(matching, "current_digest", lambda *args: "test")
    monkeypatch.setattr(matching, "verdict_prompt", lambda *args: "Beoordeel deze vacature.")


def test_opening_a_vacancy_assesses_it_and_the_explanation_swaps_in(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    job_id = add_job("Planner")
    page = client.get(f"/jobs/{job_id}").text
    assert "We beoordelen deze vacature voor je" in page and f'href="/jobs/{job_id}/oordeel"' in page
    client.get(f"/jobs/{job_id}")  # a second look asks nothing new
    assert _run() == 1
    assert store.get_job(job_id)["match_score"] == 72
    part = client.get(f"/jobs/{job_id}/oordeel").text
    assert "Veel klantcontact en planning" in part and "Ook avondwerk." in part
    assert "data-swap" not in part.split("<template")[0]  # ready: the part no longer asks to be swapped


def test_without_ai_nothing_is_asked_and_the_page_says_so(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(runtime, "manager", SimpleNamespace(client=None))
    job_id = add_job("Planner")
    page = client.get(f"/jobs/{job_id}").text
    assert "Nog niet beoordeeld" in page and "Dat gebeurt vanzelf" not in page
    assert tasks.claim_next() is None


def test_a_failed_assessment_is_not_asked_again_on_every_visit(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, "geen json")
    job_id = add_job("Planner")
    client.get(f"/jobs/{job_id}")
    task = tasks.claim_next()
    try:
        job_verdict._task(task)
    except ValueError as exc:
        tasks.fail(task["id"], str(exc))
    assert "lukte deze keer niet" in client.get(f"/jobs/{job_id}").text
    assert tasks.claim_next() is None
