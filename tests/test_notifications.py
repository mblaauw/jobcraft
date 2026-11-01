"""Strong new matches reach people by themselves, and only once."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app import digest, notifications, store, workflows
from tests.test_jobs_page import add_job, client, soup  # noqa: F401


def start_counting():
    store.meta_set("matches_seen_at", "2020-01-01T00:00:00+00:00")


def test_first_round_only_sets_the_starting_point(client):  # noqa: F811
    add_job("Oud", rank=90)
    assert notifications.refresh() == 0
    assert store.meta_get("matches_seen_at")


def test_only_strong_reachable_unvoted_jobs_count(client):  # noqa: F811
    start_counting()
    strong = add_job("Sterk", rank=90)
    add_job("Zwak", rank=40)
    add_job("Ver", location="far", rank=95)
    voted = add_job("Gestemd", rank=90)
    store.set_job_vote(voted, 1)
    assert notifications.refresh() == 1
    assert [job["id"] for job in notifications.pending()] == [strong]


def test_badge_banner_and_page_then_cleared_by_seeing(client):  # noqa: F811
    start_counting()
    add_job("Sterk", rank=90)
    notifications.refresh()
    html = soup(client.get("/jobs"))
    assert html.select_one(".nav-badge").get_text().startswith("1")
    assert "1 nieuwe sterke match" in html.select_one('a[href="/jobs/nieuw"]').get_text()
    assert "Sterk" in soup(client.get("/jobs/nieuw")).get_text()
    html = soup(client.get("/jobs"))
    assert not html.select(".nav-badge")
    assert "Je bent bij" in soup(client.get("/jobs/nieuw")).get_text()
    assert notifications.refresh() == 0


def test_acting_elsewhere_removes_it_from_the_page(client):  # noqa: F811
    start_counting()
    job_id = add_job("Sterk", rank=90)
    notifications.refresh()
    store.update_job_status(job_id, "ignored")
    assert notifications.pending() == []


@pytest.fixture()
def mail(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_FROM", "jobs@test")
    sent = []
    monkeypatch.setattr(digest, "deliver", sent.append)
    store.meta_set("digest_email", "a@b.test")
    store.meta_set("digest_enabled", "1")
    start_counting()
    return sent


def test_very_strong_match_emails_once(mail):
    add_job("Top", rank=90)
    notifications.refresh()
    notifications.refresh()
    assert len(mail) == 1


def test_good_match_only_gets_the_badge(mail):
    add_job("Goed", rank=70)
    assert notifications.refresh() == 1
    assert not mail


def test_alerts_wait_at_least_six_hours(mail):
    add_job("Eerste", rank=90)
    notifications.refresh()
    add_job("Tweede", rank=91)
    notifications.refresh()
    assert len(mail) == 1
    earlier = datetime.now(UTC) - timedelta(hours=7)
    store.meta_set("match_alert_at", earlier.isoformat())
    notifications.refresh()
    assert len(mail) == 2


def test_no_mail_without_opt_in(mail):
    store.meta_set("digest_enabled", "0")
    add_job("Top", rank=90)
    notifications.refresh()
    assert not mail


def test_mail_failure_keeps_the_badge(mail, monkeypatch):
    def broken(message):
        raise OSError("smtp down")

    monkeypatch.setattr(digest, "deliver", broken)
    add_job("Top", rank=90)
    assert notifications.refresh() == 1
    assert json.loads(store.meta_get("new_matches"))
    assert not store.meta_get("match_alert_at")


def test_finished_round_survives_a_notification_failure(client, monkeypatch):  # noqa: F811
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(workflows.matching, "match_jobs", lambda m: {"matched": 0, "signals": 0})
    monkeypatch.setattr(notifications, "refresh", broken)
    workflows._run_match()
    assert store.meta_get("match_status") == "done"


def test_accounts_see_only_their_own_count(client):  # noqa: F811
    from app import auth, tenancy

    start_counting()
    add_job("Sterk", rank=90)
    notifications.refresh()
    auth._insert_user("other", "other", "Ander", "user", "other-password-1")
    with tenancy.scope({"id": "other"}):
        assert notifications.badge_count() == 0


def test_no_configured_model_is_not_reported_as_a_failure(client, monkeypatch):  # noqa: F811
    from app import matching, ranking

    monkeypatch.setattr(matching, "current_digest", lambda *a: "test")
    monkeypatch.setattr(ranking, "rerank_all", lambda *a: 0)
    monkeypatch.setattr(ranking, "queue_depth", lambda *a: 0)
    monkeypatch.setattr(
        ranking, "escalation_candidates", lambda *a: pytest.fail("no model to ask")
    )
    monkeypatch.setattr(workflows.runtime, "manager", None)
    workflows._run_match()
    assert store.meta_get("match_status") == "done"
