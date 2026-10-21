"""Regression tests for the confirmed Phase 1 bugs."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app import docs, llm, matching, profile, ranking, sources, store, systemone, tenancy


def add_job(
    i: int, location: str, posted_at: str, description: str = "Synthetic public vacancy"
) -> int:
    return store.upsert_job(
        {
            "source_id": "fixture",
            "url": f"https://example.test/phase1/{i}",
            "title": f"Fixture job {i}",
            "company": "Fixture",
            "location": location,
            "hours": "24 uur",
            "description": description,
            "posted_at": posted_at,
            "content_hash": f"phase1-{i}",
        }
    )[0]


def test_systemone_does_not_starve_behind_skipped_jobs(monkeypatch):
    for i in range(40):
        add_job(i, "Groningen", f"2026-10-{18 - i % 10:02d}T10:00:00")
    for i in range(40, 50):
        add_job(i, "Amsterdam", "2026-09-01T10:00:00")
    calls = []

    def decide(state, *args, **kwargs):
        calls.append(state)
        return {"answers": {"fit": {"type": "score", "score": 3, "confidence": 0.9}}}, {}, 1

    monkeypatch.setattr(systemone, "enabled", lambda: True)
    monkeypatch.setattr(systemone, "decide", decide)
    monkeypatch.setattr(matching.ranking, "escalation_candidates", lambda *args, **kwargs: [])

    class NoLLM:
        def generate(self, *args, **kwargs):
            raise AssertionError("no LLM call expected")

    matching.match_jobs(NoLLM(), limit=12)
    assert len(calls) >= 10


def test_systemone_continues_after_one_job_error(monkeypatch):
    for i in range(10):
        add_job(100 + i, "Amsterdam", "2026-10-01T10:00:00")
    calls = []

    def decide(state, *args, **kwargs):
        calls.append(state)
        if len(calls) == 1:
            raise RuntimeError("single job failure")
        return {"answers": {"fit": {"type": "score", "score": 3, "confidence": 0.9}}}, {}, 1

    monkeypatch.setattr(systemone, "enabled", lambda: True)
    monkeypatch.setattr(systemone, "decide", decide)
    monkeypatch.setattr(matching.ranking, "escalation_candidates", lambda *args, **kwargs: [])

    class NoLLM:
        def generate(self, *args, **kwargs):
            raise AssertionError("no LLM call expected")

    summary = matching.match_jobs(NoLLM(), limit=12)
    assert len(calls) == 10
    assert summary["signals"] == 9
    assert summary["errors"]


def test_dates_are_normalized_at_ingest_and_in_matching_order():
    assert (
        sources.make_job("fixture", "https://example.test/a", "A", posted_at="")["posted_at"]
        is None
    )
    assert (
        sources.make_job(
            "fixture", "https://example.test/b", "B", posted_at="Mon, 13 Oct 2026 10:00:00 +0200"
        )["posted_at"]
        == "2026-10-13T08:00:00Z"
    )
    older = add_job(1, "Amsterdam", "2020-01-01T00:00:00")
    undated = add_job(2, "Amsterdam", "")
    with store.connect() as con:
        con.execute("UPDATE jobs SET first_seen=? WHERE id=?", ("2026-10-19T12:00:00Z", undated))
    rows = store.list_jobs_for_matching(2)
    assert [row["id"] for row in rows] == [undated, older]


def test_document_generation_locks_are_per_owner(monkeypatch):
    job_a = add_job(1, "Amsterdam", "2026-10-01T10:00:00")
    job_b = add_job(2, "Amsterdam", "2026-10-01T10:00:00")
    started = threading.Event()
    release = threading.Event()
    second_done = threading.Event()

    def fake_generate(mgr, job, kind):
        if job["id"] == job_a:
            started.set()
            assert release.wait(5)
        else:
            second_done.set()
        return {}

    monkeypatch.setattr(docs, "_generate_job_document", fake_generate)
    owner_a = {"id": "lock-a", "username": "a", "display_name": "A", "role": "user"}
    owner_b = {"id": "lock-b", "username": "b", "display_name": "B", "role": "user"}

    def run(owner, job_id):
        with tenancy.scope(owner):
            docs.generate_for_job(None, store.get_job(job_id), "resume")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, owner_a, job_a)
        assert started.wait(2)
        second = pool.submit(run, owner_b, job_b)
        assert second_done.wait(2)
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)


def test_interview_context_lists_open_topics(monkeypatch):
    store.create_session("topics-session", "Topics", "interviewer", "fixture")
    prompts = []
    manager = llm.ServerManager()
    manager.client = SimpleNamespace(
        session_info=lambda _: {},
        prompt=lambda _, text: prompts.append(text),
        wait=lambda _: None,
        messages=lambda _: [
            {
                "type": "assistant",
                "id": "assistant-topics",
                "content": [{"type": "text", "text": "Volgende vraag?"}],
            }
        ],
    )
    monkeypatch.setattr(llm.interview_state, "open_topics", lambda: ["Fysieke belasting"])
    manager.run_turn("topics-session", "Antwoord")
    assert "Nog open onderwerpen: Fysieke belasting" in prompts[0]


def test_unknown_interview_topic_returns_400():
    import pytest
    from fastapi import HTTPException

    from app import main

    with pytest.raises(HTTPException) as error:
        main.interview_toggle(topic_id="does-not-exist", status="covered", session_id="")
    assert error.value.status_code == 400


def test_interview_context_is_sent_once_until_the_profile_changes():
    store.create_session("context-session", "Context", "interviewer", "fixture")
    prompts = []
    manager = llm.ServerManager()
    manager.client = SimpleNamespace(
        session_info=lambda _: {},
        prompt=lambda _, text: prompts.append(text),
        wait=lambda _: None,
        messages=lambda _: [
            {
                "type": "assistant",
                "id": "assistant-1",
                "content": [{"type": "text", "text": "Volgende vraag?"}],
            }
        ],
    )
    profile.write_profile("## Werk\nPubliek profiel", "fixture")
    manager.run_turn("context-session", "Eerste antwoord")
    manager.run_turn("context-session", "Tweede antwoord")
    assert prompts[0].startswith("[ACCOUNT_CONTEXT]")
    assert prompts[1] == "Tweede antwoord"
    profile.write_profile("## Werk\nGewijzigd profiel", "fixture")
    manager.run_turn("context-session", "Derde antwoord")
    assert prompts[2].startswith("[ACCOUNT_CONTEXT]")


def _add_source(status: str, scanned_at: str) -> None:
    with store.connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO sources(id,name,url,kind,enabled,last_scan_at,last_status) "
            "VALUES ('fixture','Fixture','https://example.test','rss',1,?,?)",
            (scanned_at, status),
        )


def test_stale_jobs_are_hidden_unless_saved_or_scan_failed():
    job_id = add_job(1, "Amsterdam", "2026-10-01T10:00:00")
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with store.connect() as con:
        con.execute("UPDATE jobs SET last_seen=?, source_id='fixture' WHERE id=?", (old, job_id))
    _add_source("ok", datetime.now(UTC).isoformat())
    assert store.list_jobs(50) == []
    assert [row["id"] for row in store.list_jobs(50, include_stale=True)] == [job_id]
    assert store.list_jobs_for_matching(50) == []

    store.update_job_status(job_id, "saved")
    assert [row["id"] for row in store.list_jobs(50)] == [job_id]

    store.update_job_status(job_id, "new")
    _add_source("error", datetime.now(UTC).isoformat())
    assert [row["id"] for row in store.list_jobs(50)] == [job_id]


def test_match_jobs_stores_the_raw_verdict(monkeypatch):
    job_id = add_job(1, "Amsterdam", "2026-10-01T10:00:00")
    monkeypatch.setattr(systemone, "enabled", lambda: False)
    monkeypatch.setattr(
        matching.ranking, "escalation_candidates", lambda *args, **kwargs: [store.get_job(job_id)]
    )
    monkeypatch.setattr(matching.ranking, "queue_depth", lambda *args: 0)

    class Fake:
        def generate(self, *args, **kwargs):
            return (
                json.dumps(
                    {
                        "score": 90,
                        "why": "Sterke match",
                        "concerns": [],
                        "questions_for_employer": [],
                    }
                ),
                {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
            )

    matching.match_jobs(Fake(), limit=1)
    assert store.get_job(job_id)["match_score"] == 90


def test_document_frequency_and_travel_are_cached(monkeypatch):
    from app import feedback as feedback_module

    add_job(1, "Amsterdam", "2026-10-01T10:00:00", description="planten groen buiten")
    feedback_module.refresh_document_frequency()
    cached = store.load_token_df()
    assert cached and cached[1] == 1
    corpus, total = feedback_module._document_frequency()
    assert total == 1 and corpus["planten"] == 1

    calls = []

    def assess(*args, **kwargs):
        calls.append(args)
        return {"in_range": True, "label": "fixture", "distance_km": 1.0}

    monkeypatch.setattr(ranking.geo, "assess", assess)
    job = store.get_job(1)
    first = ranking.travel_of(job)
    second = ranking.travel_of(job)
    assert first == second and len(calls) == 1
    with store.connect() as con:
        con.execute("UPDATE jobs SET location='Utrecht' WHERE id=1")
    ranking.travel_of(store.get_job(1))
    assert len(calls) == 2


def test_old_blended_verdict_is_recovered_once():
    job_id = add_job(1, "Amsterdam", "2026-10-01T10:00:00")
    store.update_job_match(
        job_id, 70.0, json.dumps({"why": "old", "rule_score": 40.0}), "old-digest"
    )
    with store.connect() as con:
        con.execute("DELETE FROM meta WHERE key='raw_verdicts_v1'")
        store._recover_raw_verdicts(con)
        store._recover_raw_verdicts(con)
    assert store.get_job(job_id)["match_score"] == 90.0


def test_shared_skip_rule_agrees_between_jev_and_escalation():
    job = {
        "id": 1,
        "vote": 0,
        "status": "new",
        "location": "Groningen",
        "lat": None,
        "lon": None,
        "description": "Synthetic",
        "title": "Fixture",
    }
    assert ranking.excluded_from_matching(job) is True


def test_account_source_preferences_filter_jobs_and_matching():
    job_id = add_job(900, "Amsterdam", "2026-10-01T10:00:00")
    store.set_user_source("fixture", False)
    assert store.list_jobs(50) == []
    assert store.list_jobs_for_matching(50) == []
    store.set_user_source("fixture", True)
    assert any(row["id"] == job_id for row in store.list_jobs(50))
