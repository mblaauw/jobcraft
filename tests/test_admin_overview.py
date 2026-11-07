"""The administrator's home answers three questions and lists what needs attention."""

from __future__ import annotations

import json

from test_auth_events import _bootstrap_password, _full_app_client
from test_jobs_page import client  # noqa: F401

from app import store, tasks


def _admin():
    return _full_app_client("admin", _bootstrap_password("admin"))


def test_the_overview_shows_the_three_cards_and_a_quiet_attention_list(client):  # noqa: F811
    html = _admin().get("/admin").text
    for title in ("Draait", "Mensen", "AI vandaag", "Aandacht nodig"):
        assert title in html
    assert "in dit proces" in html  # the combined role runs the worker here
    assert "<dt>Zonder profiel</dt><dd>1</dd>" in html  # test-user; administrators are not expected to have one
    assert "Niets. Bronnen halen op" in html
    assert "Bestanden en ruwe voorkeuren" not in html and "Bronoverzicht" not in html


def test_attention_lists_failing_sources_failed_work_and_guessers(client):  # noqa: F811
    with store.connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO sources(id,name,url,kind,enabled,last_scan_at,last_status) "
            "VALUES ('fixture','Fixture','https://example.test','rss',1,'2026-11-06T07:30:00+00:00','error')"
        )
    store.meta_set("source_health", json.dumps({"fixture": {"failures": 3, "reason": "HTTPStatusError: 403 Forbidden", "last_ok": None}}))
    task_id = tasks.enqueue("vacancy", owner=None, dedupe_key="vacancy:test")
    tasks.claim_next()
    tasks.fail(task_id, "TimeoutError: read timed out")
    for _ in range(5):
        store.record_auth_event("login_failed", username="gast", ip="203.0.113.9")
    html = _admin().get("/admin").text
    assert "Fixture: 3 rondes achter elkaar mislukt, HTTPStatusError" in html
    assert "Vacaturetekst opmaken mislukte 1 keer: TimeoutError" in html
    assert "read timed out" not in html  # the message may quote a person; the name may not
    assert "5 mislukte inlogpogingen op 1 namen vanaf 203.0.113.9" in html
    assert "<dd>Nee</dd>" not in html


def test_settings_show_the_schedule_and_save_it_back_here(client):  # noqa: F811
    admin = _admin()
    html = admin.get("/admin/instellingen").text
    assert "Automatisch ophalen" in html and "AUTH_LOG_DAYS" in html and "Bestanden" in html
    from test_auth import csrf_of

    answer = admin.post(
        "/sources/schedule",
        data={"_csrf": csrf_of(html), "enabled": "1", "times": "06:00,18:00", "next": "/admin/instellingen"},
        follow_redirects=False,
    )
    assert answer.status_code == 303 and answer.headers["location"] == "/admin/instellingen?opgeslagen=1"
    assert store.meta_get("scan_schedule_times") == "06:00,18:00"
    assert "Planning opgeslagen" in admin.get("/admin/instellingen?opgeslagen=1").text
    assert "Automatisch ophalen stel je in bij" in admin.get("/sources").text


def test_the_ai_page_carries_the_usage_tables(client):  # noqa: F811
    store.record_usage(None, "matching", 1000, 100, 0.0, agent="matcher")
    html = _admin().get("/admin/ai").text
    assert "Gebruik per soort" in html and "Per dag" in html and "1.100" in html


def test_a_schedule_saved_per_account_before_is_carried_over_once(client):  # noqa: F811
    with store.connect() as con:
        con.execute("DELETE FROM meta WHERE key IN ('scan_schedule_enabled','scan_schedule_times')")
        con.execute("INSERT OR REPLACE INTO user_meta(owner,key,value) VALUES ('someone','scan_schedule_times','06:15,19:45')")
    store.init_db()
    assert store.meta_get("scan_schedule_times") == "06:15,19:45"
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) AS n FROM user_meta WHERE key='scan_schedule_times'").fetchone()["n"] == 0


def test_ai_card_measures_each_limit_per_account_and_counts_jev_for_everyone(client, monkeypatch):  # noqa: F811
    from app import tenancy

    monkeypatch.setenv("AI_DAILY_TOKEN_LIMIT", "1000")
    monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "2")
    with tenancy.scope({"id": "seeker-a"}):
        store.record_usage(None, "matching", 300, 100, 0.0, agent="matcher")
        for _ in range(2):
            store.record_usage(None, "systemone", 50000, 5000, 0.01, agent="jev")
    with tenancy.scope({"id": "seeker-b"}):
        store.record_usage(None, "documents", 100, 100, 0.0, agent="writer")
        store.record_usage(None, "systemone", 50000, 5000, 0.01, agent="jev")
    admin = _admin()
    html = admin.get("/admin").text
    assert "600 tokens" in html  # the two accounts without their 165.000 Jev tokens
    assert "<dt>Drukste account</dt><dd>40% van 1.000</dd>" in html  # seeker-a, not the total
    assert "<dd>3, plafond bereikt bij 1 account</dd>" in html  # Jev calls of every account; seeker-a is at its limit
    assert "niet alles meldt een prijs" in html  # matching and documents booked at $0
    monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "0")  # matching then leaves no Jev call for anyone
    with store.connect() as con:
        seeker = con.execute("SELECT id FROM users WHERE username='test-user'").fetchone()["id"]
    store.set_profile("# Profiel", owner=seeker)
    # Every active account with a profile, not the usage rows of unknown or shared owners.
    assert "<dd>3, plafond bereikt bij 1 account</dd>" in admin.get("/admin").text


def test_attention_drops_work_that_succeeded_since_and_names_restarts(client):  # noqa: F811
    old = tasks.enqueue("scan", owner=None, dedupe_key="scan")
    tasks.claim_next()
    tasks.fail(old, "TimeoutError: read timed out")
    done = tasks.enqueue("scan", owner=None, dedupe_key="scan")
    tasks.claim_next()
    tasks.finish(done)
    rerank = tasks.enqueue("rerank", owner="seeker-a", dedupe_key="rerank:seeker-a")
    tasks.claim_next()
    tasks.fail(rerank, "Onderbroken door een herstart. Probeer opnieuw.")
    for error in ("TimeoutError: read timed out", "ValueError: herstart na verlof", "Onderbroken door een herstart. Probeer opnieuw."):
        task_id = tasks.enqueue("document", owner="seeker-b")
        tasks.claim_next()
        tasks.fail(task_id, error)
    html = _admin().get("/admin").text
    assert "Ophalen" not in html  # the scan ran fine afterwards
    assert "Opnieuw rangschikken 1 keer onderbroken door een herstart" in html
    assert "rerank" not in html
    # A message that merely contains the word is a real failure; the name of the latest one shows.
    assert "Documenten maken mislukte 2 keer: ValueError, 1 keer onderbroken door een herstart" in html
    assert "verlof" not in html
