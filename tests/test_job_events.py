"""The history of each vacancy: every change kept, in order, per account."""

from __future__ import annotations

import json

from app import account_data, applications, auth, judgements, ranking, store, tenancy
from app.migrations import m0007_job_events
from app.scoring.route import Route
from tests.test_jobs_page import add_job, client  # noqa: F401


def _kinds(job_id=None):
    return [(event["kind"], event["value"], event["previous"]) for event in store.list_job_events(job_id)]


def test_a_status_change_is_kept_once_with_where_it_came_from(client):  # noqa: F811
    job_id = add_job()
    store.update_job_status(job_id, "saved")
    store.update_job_status(job_id, "saved")  # no change, no event
    store.update_job_status(job_id, "applied")
    store.update_job_status(job_id, "saved")
    store.update_job_status(job_id, "applied")
    assert _kinds(job_id) == [
        ("status", "saved", "new"),
        ("status", "applied", "saved"),
        ("status", "saved", "applied"),
        ("status", "applied", "saved"),
    ]
    assert store.status_funnel() == {"saved": 1, "applied": 1}  # vacancies, not clicks


def test_the_board_and_the_status_buttons_both_leave_a_trace(client):  # noqa: F811
    job_id = add_job()
    client.post(f"/jobs/{job_id}/status", data={"status": "saved"})
    applications.on_the_list(store.get_job(add_job("Tweede")))
    assert [event["value"] for event in store.list_job_events(kind="status")] == ["saved", "saved"]


def test_judgements_reasons_and_verdicts_are_codes_and_numbers(client):  # noqa: F811
    job_id = add_job()
    judgements.answer(job_id, "nee")
    assert judgements.set_reason(job_id, "ver")
    store.update_job_match(job_id, 42.5, json.dumps({"why": "Een lange uitleg"}), "digest")
    events = store.list_job_events(job_id)
    assert [(e["kind"], e["value"]) for e in events] == [("judgement", "nee"), ("reason", "ver"), ("verdict", "42.5")]
    assert json.loads(events[0]["detail"]) == {"asked": False}
    assert "uitleg" not in json.dumps(events)  # no prose in the history


def test_only_route_changes_are_stored(client):  # noqa: F811
    first, second = add_job("Een"), add_job("Twee")
    jobs = {row["id"]: row for row in store.list_jobs_for_matching()}

    def remember(*outcomes):
        decided = [(Route(outcome, code, ""), None, jobs[job_id]) for job_id, (outcome, code) in zip((first, second), outcomes, strict=True)]
        ranking._remember_routes("digest", decided)

    remember(("wait", "no_signals"), ("wait", "no_signals"))
    remember(("wait", "no_signals"), ("act", "sure"))
    remember(("wait", "no_signals"), ("act", "sure"))
    assert _kinds() == [
        ("route", "wait:no_signals", None),
        ("route", "wait:no_signals", None),
        ("route", "act:sure", "wait:no_signals"),
    ]
    store.record_routes({999999: "act:sure"})
    assert len(_kinds()) == 3  # a vanished vacancy is skipped


def test_accounts_see_and_take_only_their_own_history(client):  # noqa: F811
    job_id = add_job()
    store.update_job_status(job_id, "saved")
    auth._insert_user("other", "other", "Ander", "user", "other-password-1")
    with tenancy.scope({"id": "other"}):
        assert store.list_job_events() == []
        store.update_job_status(job_id, "ignored")
        exported = account_data.export_zip()
        account_data.delete_account()
    assert b"job_events.json" in exported
    assert _kinds(job_id) == [("status", "saved", "new")]
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM job_events WHERE owner='other'").fetchone()[0] == 0


def test_the_migration_carries_over_what_was_already_stored(client):  # noqa: F811
    job_id = add_job()
    store.meta_set(f"application:{job_id}", json.dumps({"status": "applied", "since": {"applied": "2026-11-02", "saved": "2026-11-01"}}))
    store.meta_set(f"judgement:{job_id}", json.dumps({"answer": "ja", "at": "2026-11-03T09:00:00+00:00"}))
    store.meta_set("application:999999", json.dumps({"since": {"saved": "2026-11-01"}}))  # vacancy is gone
    with store.connect() as con:
        con.execute("DROP TABLE job_events")
        m0007_job_events.up(con)
        m0007_job_events.up(con)  # a replay adds nothing
    assert [(e["kind"], e["value"], e["at"][:10]) for e in store.list_job_events()] == [
        ("status", "saved", "2026-11-01"),
        ("status", "applied", "2026-11-02"),
        ("judgement", "ja", "2026-11-03"),
    ]
