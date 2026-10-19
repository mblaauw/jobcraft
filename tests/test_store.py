from __future__ import annotations

import pytest

from app import config, store


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    store.init_db()
    return tmp_path


def test_session_messages_and_dedupe(database):
    store.create_session("ses_1", "Test", "interviewer", "opencode-go/deepseek")
    assert store.get_session("ses_1")["title"] == "Test"

    added = store.add_message("ses_1", "msg_a", "user", "Hallo")
    duplicated = store.add_message("ses_1", "msg_a", "user", "Hallo")
    store.add_message("ses_1", "msg_b", "assistant", "Welke rol past?", model="m")
    assert added is True
    assert duplicated is False
    rows = store.list_messages("ses_1")
    assert [row["role"] for row in rows] == ["user", "assistant"]


def test_usage_total(database):
    store.record_usage("ses_1", "interview", 100, 20, 0.001)
    store.record_usage(None, "consolidation", 4000, 500, 0.02)
    totals = store.usage_total()
    assert totals["tin"] == 4100
    assert totals["tout"] == 520
    assert totals["calls"] == 2
    assert round(totals["cost"], 4) == 0.021


def test_meta_roundtrip(database):
    assert store.meta_get("missing") is None
    store.meta_set("status", "running")
    store.meta_set("status", "done")
    assert store.meta_get("status") == "done"


def test_message_visibility_flag(database):
    store.create_session("ses_v", "Visible", "interviewer", "opencode-go/deepseek")
    store.add_message("ses_v", "msg_hidden", "user", "kickoff", hidden=1)
    store.add_message("ses_v", "msg_visible", "assistant", "question")
    rows = store.list_messages("ses_v")
    assert [row["hidden"] for row in rows] == [1, 0]


def test_job_upsert_dedupes_and_counts(database):
    job = {
        "source_id": "test",
        "url": "https://example.org/vacature/1",
        "title": "Medewerker groen",
        "company": "Groenbedrijf",
        "location": "",
        "hours": "",
        "description": "Buiten werken.",
        "posted_at": "2026-10-01T10:00:00",
        "content_hash": "abc",
    }
    job_id, created = store.upsert_job(job)
    assert created is True
    again_id, created_again = store.upsert_job({**job, "title": "Medewerker groen (nieuw)"})
    assert created_again is False
    assert again_id == job_id
    assert store.get_job(job_id)["title"] == "Medewerker groen (nieuw)"
    assert store.count_jobs()["total"] == 1
