"""Opening a vacancy follows the route: sure ones buy nothing, new ones ask Jev first."""

from __future__ import annotations

import json

from test_job_verdict import VERDICT, _ai, _run
from test_jobs_page import add_job, client  # noqa: F401

from app import job_verdict, matching, ranking, store, systemone, tasks

SURE = {"fit": 80, "confidence": 0.9, "escalate": False}
UNSURE = {"fit": 50, "confidence": 0.4, "escalate": True}


def _signals(job_id, signals, rank=70):
    store.set_rank_scores({job_id: rank})
    store.update_job_signals(job_id, rank, json.dumps(signals), "key", signals["confidence"])


def test_a_sure_vacancy_is_explained_without_a_verdict_and_can_be_deepened(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    job_id = add_job("Planner")
    _signals(job_id, SURE)
    page = client.get(f"/jobs/{job_id}").text
    assert "past dit <strong>goed</strong> bij je" in page and "90% zeker" in page
    assert 'id="detail-deeper"' in page and "We beoordelen deze vacature" not in page
    assert tasks.claim_next() is None  # nothing bought
    answer = client.post(f"/jobs/{job_id}/oordeel/dieper", follow_redirects=False)
    assert answer.status_code == 303 and job_verdict.wanted(job_id)
    assert "We beoordelen deze vacature voor je" in client.get(f"/jobs/{job_id}").text
    assert _run() == 1
    assert store.get_job(job_id)["match_score"] == 72 and not job_verdict.wanted(job_id)
    assert "Veel klantcontact en planning" in client.get(f"/jobs/{job_id}/oordeel").text


def test_a_low_ranked_vacancy_says_so_and_buys_nothing(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    job_id = add_job("Planner")
    _signals(job_id, UNSURE, rank=20)
    page = client.get(f"/jobs/{job_id}").text
    assert "past dit <strong>redelijk</strong> bij je. Deze vacature staat laag in je lijst" in page
    assert 'id="detail-deeper"' in page
    assert tasks.claim_next() is None


def test_structural_evenings_hide_the_day_work_chip(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    evenings = add_job("Planner")
    _signals(evenings, {**SURE, "noul": {"day_hours": 0.9, "evening_weekend": 0.9}})
    page = client.get(f"/jobs/{evenings}").text
    assert "Avond- of weekendwerk" in page and ">Dagwerk<" not in page
    days = add_job("Teamleider", 2)
    _signals(days, {**SURE, "noul": {"day_hours": 0.9, "evening_weekend": 0.1}})
    page = client.get(f"/jobs/{days}").text
    assert ">Dagwerk<" in page and "Avond- of weekendwerk" not in page


def test_an_unsure_vacancy_is_assessed_as_before(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    job_id = add_job("Planner")
    _signals(job_id, UNSURE)
    assert "We beoordelen deze vacature voor je" in client.get(f"/jobs/{job_id}").text
    assert _run() == 1 and store.get_job(job_id)["match_score"] == 72


def test_without_a_quick_assessment_jev_goes_first_and_may_have_the_last_word(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    job_id = add_job("Planner")
    store.set_rank_scores({job_id: 70})
    asked = []

    def signal_one(job, *args, **kwargs):
        asked.append(job["id"])
        _signals(job["id"], SURE)
        return SURE

    monkeypatch.setattr(matching, "signal_one", signal_one)
    monkeypatch.setattr(ranking, "after_signals", lambda job_id, signals: None)
    page = client.get(f"/jobs/{job_id}").text
    assert "We beoordelen deze vacature voor je" in page  # Jev first, in the background
    assert _run() == 1 and asked == [job_id]
    assert store.get_job(job_id)["match_score"] is None  # Jev was sure: no verdict bought
    assert "past dit <strong>goed</strong> bij je" in client.get(f"/jobs/{job_id}/oordeel").text
    assert tasks.claim_next() is None


def test_when_jev_has_no_room_the_matcher_judges_as_before(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    monkeypatch.setattr(matching, "signal_one", lambda job, *args, **kwargs: None)
    job_id = add_job("Planner")
    client.get(f"/jobs/{job_id}")
    assert _run() == 1 and store.get_job(job_id)["match_score"] == 72


def test_signal_one_asks_facts_once_and_stops_when_the_day_is_spent(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    monkeypatch.setattr(matching, "current_digest", lambda *args: "d")
    job_id = add_job("Planner")
    store.set_rank_scores({job_id: 70})
    calls = []

    def ask(job, state, questions):
        calls.append(sorted(questions))
        if "fit" in questions:
            return {"fit": {"type": "score", "score": 3, "confidence": 0.9, "probabilities": {"3": 0.9, "4": 0.1}}}
        return {"day_hours": {"type": "noul", "noul": 0.9}}

    monkeypatch.setattr(matching, "_ask_system_one", ask)
    signals = matching.signal_one(dict(store.get_job(job_id)))
    assert signals["fit"] == 75.0 and signals["noul"]["day_hours"] == 0.9 and len(calls) == 2
    assert ranking.signals_of(store.get_job(job_id))["fit"] == 75.0
    calls.clear()
    assert matching.signal_one(dict(store.get_job(job_id)))["fit"] == 75.0
    assert calls == [["category", "fit"]]  # the facts are shared and known
    monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "0")
    assert matching.signal_one(dict(store.get_job(job_id))) is None


def test_the_person_may_retry_a_failed_assessment_at_once(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, "geen json")
    job_id = add_job("Planner")
    _signals(job_id, UNSURE)
    client.get(f"/jobs/{job_id}")
    task = tasks.claim_next()
    tasks.fail(task["id"], "broken")
    assert "lukte deze keer niet" in client.get(f"/jobs/{job_id}").text
    client.post(f"/jobs/{job_id}/oordeel/dieper", follow_redirects=False)
    assert tasks.claim_next() is not None


def test_an_ignored_vacancy_buys_nothing_and_a_spent_budget_hides_the_button(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    ignored = add_job("Planner")
    _signals(ignored, UNSURE)
    store.update_job_status(ignored, "ignored")
    client.get(f"/jobs/{ignored}")
    assert tasks.claim_next() is None
    sure = add_job("Teamleider", 2)
    _signals(sure, SURE)
    monkeypatch.setattr(job_verdict, "budget_reached", lambda: True)
    page = client.get(f"/jobs/{sure}").text
    assert "past dit <strong>goed</strong> bij je" in page and 'id="detail-deeper"' not in page
    assert "daglimiet" in page


def test_a_budget_crossed_meanwhile_stops_the_task_before_it_pays(client, monkeypatch):  # noqa: F811
    _ai(monkeypatch, VERDICT)
    job_id = add_job("Planner")
    _signals(job_id, UNSURE)
    client.get(f"/jobs/{job_id}")
    monkeypatch.setattr(job_verdict, "budget_reached", lambda: True)
    assert _run() == 1 and store.get_job(job_id)["match_score"] is None


def test_after_signals_reblends_without_a_full_pass(client, monkeypatch):  # noqa: F811
    job_id = add_job("Planner")
    store.set_rank_scores({job_id: 40})
    store.meta_set(ranking.PARTS_KEY, json.dumps({str(job_id): [40.0, None, None]}))
    store.meta_set("fit_calibration", json.dumps({"slope": 1.0, "intercept": 0.0}))
    ranking.after_signals(job_id, SURE)
    assert store.get_job(job_id)["rank_score"] > 40
