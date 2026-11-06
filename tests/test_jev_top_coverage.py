"""Jev batches keep going until the best-ranked vacancies are assessed, within the day's calls."""

from __future__ import annotations

import json

import pytest
from test_shared_facts import FakeJev, owner_id

from app import matching, profile, ranking, store, systemone, tenancy
from tests.test_jobs_page import add_job, client  # noqa: F401


@pytest.fixture()
def jev(client, monkeypatch):  # noqa: F811
    fake = FakeJev()
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    monkeypatch.setattr(systemone, "decide", fake)
    monkeypatch.setattr(matching, "current_digest", lambda *a: "test")
    monkeypatch.setattr(ranking, "rerank_all", lambda *a: 0)
    monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "100")
    monkeypatch.setenv("SYSTEMONE_BATCH", "2")  # one vacancy per batch: facts and fit
    monkeypatch.setenv("SYSTEMONE_FIT_TOP", "10")
    return fake


def three_jobs():
    for index, title in enumerate(("Hovenier", "Boomverzorger", "Groenbeheerder")):
        add_job(title, rank=90 - index)


def test_batches_keep_going_until_every_job_above_the_fit_line_has_its_answer(jev):
    three_jobs()
    with tenancy.scope({"id": owner_id()}):
        summary = matching.match_jobs(None)
        signalled = [job for job in store.list_jobs_for_matching() if ranking.signals_of(job)]
    assert summary["signals"] == 3 and len(signalled) == 3
    assert len(jev.calls) == 6  # three batches of two, not one batch of two


def test_the_daily_limit_still_ends_the_batches(jev, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "3")
    three_jobs()
    with tenancy.scope({"id": owner_id()}):
        summary = matching.match_jobs(None)
    assert len(jev.calls) == 3 and summary["signals"] == 2  # the second vacancy got facts only


def test_a_failing_jev_stops_after_three_failures_and_does_not_loop(jev, monkeypatch):
    def broken(state, questions=None, model=None):
        raise RuntimeError("down")

    monkeypatch.setattr(systemone, "decide", broken)
    three_jobs()
    with tenancy.scope({"id": owner_id()}):
        summary = matching.match_jobs(None)
    assert summary["errors"][-1] == "systemone gestopt na 3 opeenvolgende fouten"
    assert summary["errors"].count("systemone gestopt na 3 opeenvolgende fouten") == 1


def test_a_batch_that_answers_nothing_does_not_loop(jev, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_BATCH", "0")
    reranks = []
    monkeypatch.setattr(ranking, "rerank_all", lambda *a: reranks.append(1))
    three_jobs()
    with tenancy.scope({"id": owner_id()}):
        matching.match_jobs(None)
    assert jev.calls == [] and len(reranks) <= 3  # one Jev round plus the two reranks of the match round


def test_a_covered_top_runs_one_round_only(jev):
    three_jobs()
    with tenancy.scope({"id": owner_id()}):
        matching.match_jobs(None)
        before = len(jev.calls)
        matching.match_jobs(None)
    assert len(jev.calls) == before


def test_the_overview_names_an_account_whose_top_waits_for_jev(client):  # noqa: F811
    from test_auth_events import _bootstrap_password, _full_app_client

    owner = owner_id()
    with tenancy.scope({"id": owner}):
        profile.write_profile("# CV\n\n## Over mij\nHovenier.\n", "test")
        store.meta_set("top_wait", json.dumps({"waiting": 179, "of": 200}))
    admin = _full_app_client("admin", _bootstrap_password("admin"))
    html = admin.get("/admin").text
    assert "179 van de 200 best gerangschikte vacatures wachten nog op een snelle beoordeling" in html
    assert 'href="/admin/ai">AI</a>' in html
    with tenancy.scope({"id": owner}):
        store.meta_set("top_wait", json.dumps({"waiting": 5, "of": 200}))  # between two batches: normal
    assert "best gerangschikte vacatures" not in admin.get("/admin").text
