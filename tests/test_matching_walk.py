"""The matching walk covers the whole live catalogue; full walks stay out of web requests."""

from __future__ import annotations

import json

from app import ranking, store, tasks, tenancy
from app.store import job_walk
from tests.test_jobs_page import add_job, client  # noqa: F401
from tests.test_phase2_start import _add_jobs, _choices, flow  # noqa: F401

REAL_RERANK = ranking.rerank_all  # the client fixture swaps it for a stand-in


def test_the_walk_takes_every_live_vacancy_and_says_when_its_cap_binds(client, monkeypatch):  # noqa: F811
    ids = [add_job(f"Vacature {n}", date=f"2026-10-0{n}") for n in range(1, 5)]
    warnings = []
    monkeypatch.setattr(job_walk.log, "warning", lambda *args: warnings.append(args))
    assert {row["id"] for row in store.list_jobs_for_matching()} == set(ids)
    assert warnings == []

    monkeypatch.setattr(job_walk, "MATCHING_LIMIT", 3)
    assert [row["id"] for row in store.list_jobs_for_matching()] == ids[:0:-1]  # newest first
    assert len(warnings) == 1 and warnings[0][1] == 3
    assert len(store.list_jobs_for_matching(2)) == 2  # an asked-for limit is not a cap that binds
    assert len(warnings) == 1


def test_a_partial_rerank_ranks_the_newest_and_leaves_routing_to_the_worker(client, monkeypatch):  # noqa: F811
    for n in range(1, 4):
        add_job(f"Vacature {n}", date=f"2026-10-0{n}")
    routed = []
    monkeypatch.setattr(ranking, "rerank_all", REAL_RERANK)
    monkeypatch.setattr(ranking, "routed", lambda *args, **kwargs: routed.append(1) or [])
    assert ranking.rerank_all(limit=2) == 2
    assert routed == []
    assert ranking.rerank_all() == 3
    assert routed == [1]


def test_onboarding_ranks_part_in_the_request_and_the_whole_in_the_worker(flow, monkeypatch):  # noqa: F811
    passes = []
    monkeypatch.setattr(ranking, "rerank_all", lambda *args, **kwargs: passes.append(kwargs.get("limit")) or 0)
    _add_jobs()
    _choices(flow)
    assert passes == [store.WEB_WALK_LIMIT]
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    assert [task["kind"] for task in tasks.list_for_owner(owner)] == ["rerank"]

    with tenancy.scope({"id": owner}):
        queue = json.loads(store.meta_get("start_swipe"))
    for job_id in queue:
        flow.post(f"/start/vacatures/{job_id}", data={"answer": "overslaan"}, follow_redirects=False)
    flow.post("/start/vacatures/stoppen", follow_redirects=False)
    assert passes == [store.WEB_WALK_LIMIT]  # the end of the swipes ranks in the worker only
