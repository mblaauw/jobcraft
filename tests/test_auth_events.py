"""Who signed in when and from where: recorded on every path, shown to administrators only."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from test_auth import _db, csrf_of, env, login, make_env, user_id  # noqa: F401
from test_jobs_page import client  # noqa: F401

from app import config, main, store
from app.auth import activity

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15"


def events(context, **where):
    with _db(context) as con:
        clauses = " AND ".join(f"{key}=?" for key in where) or "1=1"
        return [dict(row) for row in con.execute(f"SELECT * FROM auth_events WHERE {clauses} ORDER BY id", tuple(where.values()))]


def test_a_sign_in_a_failure_and_a_sign_out_are_recorded_with_address_and_browser(env):  # noqa: F811
    env.client.headers["user-agent"] = UA
    assert login(env.client, "owner", "fout").status_code == 401
    assert login(env.client, "nobody", "fout").status_code == 401
    assert login(env.client, "owner", "test-password").status_code == 303
    token = csrf_of(env.client.get("/account").text)
    env.client.post("/logout", data={"_csrf": token}, follow_redirects=False)
    rows = events(env)
    assert [row["kind"] for row in rows] == ["login_failed", "login_failed", "login_ok", "logout"]
    assert rows[0]["user_id"] == user_id(env, "owner") and rows[0]["username"] == "owner"
    assert rows[1]["user_id"] is None and rows[1]["username"] == "nobody"
    assert rows[2]["username"] is None  # the typed name is kept for failures only
    assert all(row["ip"] == "testclient" and row["user_agent"] == UA for row in rows)
    assert rows[3]["user_id"] == user_id(env, "owner")


def test_admin_actions_name_the_administrator(env):  # noqa: F811
    login(env.client, "admin", env.password("admin"))
    target = user_id(env, "test-user")
    page = env.client.get("/users").text
    env.client.post(f"/users/{target}/reset-password", data={"_csrf": csrf_of(page), "bevestig": "1"}, follow_redirects=False)
    env.client.post(f"/users/{target}/deactivate", data={"_csrf": csrf_of(page), "bevestig": "1"}, follow_redirects=False)
    env.client.post(f"/users/{target}/activate", data={"_csrf": csrf_of(page)}, follow_redirects=False)
    rows = events(env, user_id=target)
    assert [row["kind"] for row in rows] == ["password_reset", "deactivated", "activated"]
    assert {row["actor_id"] for row in rows} == {user_id(env, "admin")}


def test_the_users_page_shows_where_each_account_last_signed_in(env):  # noqa: F811
    env.client.headers["user-agent"] = UA
    login(env.client, "owner", "fout")
    login(env.client, "owner", "test-password")
    admin = env.other_client()
    login(admin, "admin", env.password("admin"))  # a bootstrap password works once
    html = admin.get("/users").text
    assert "vanaf <code>testclient</code>, Safari op Mac" in html
    assert "1 mislukte poging in 24 uur" in html
    assert 'href="/users/activiteit"' in html


def test_the_activity_page_lists_events_and_groups_guessers_for_administrators_only(env):  # noqa: F811
    for name in ("owner", "nobody", "someone", "else"):
        login(env.client, name, "fout")
    login(env.client, "owner", "test-password")
    assert env.client.get("/users/activiteit").status_code == 403
    admin = env.other_client()
    login(admin, "admin", env.password("admin"))
    html = admin.get("/users/activiteit").text
    assert "Inloggen mislukt" in html and "Ingelogd" in html
    assert html.count("<code>testclient</code>") >= 5
    assert "Mislukte pogingen, laatste 24 uur" in html
    assert re.search(r"<td class=\"num\">4</td><td class=\"num\">4</td>", html)  # four attempts, four names
    only_failed = admin.get("/users/activiteit?soort=login_failed&periode=dag").text
    assert "Ingelogd</td>" not in only_failed and "nobody" in only_failed
    one_account = admin.get(f"/users/activiteit?account={user_id(env, 'owner')}").text
    assert "nobody" not in one_account and "Ingelogd" in one_account


def test_old_events_are_pruned_and_a_deleted_account_leaves_none(env, monkeypatch):  # noqa: F811
    login(env.client, "owner", "test-password")
    old = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    with _db(env) as con:
        con.execute("INSERT INTO auth_events(at, kind, user_id, ip) VALUES (?, 'login_ok', ?, '1.2.3.4')", (old, user_id(env, "owner")))
        con.commit()
    assert store.prune_auth_events() == 1
    assert len(events(env)) == 1
    monkeypatch.setenv("AUTH_LOG_DAYS", "0")
    assert store.auth_events.retention_days() == 1


def test_browser_labels_say_something_a_person_recognises():
    assert activity.browser_label(UA) == "Safari op Mac"
    assert activity.browser_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/120.0 Mobile/15E148 Safari/604.1") == "Chrome op iPhone"
    assert activity.browser_label("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Edg/120.0") == "Edge op Windows"
    assert activity.browser_label("curl/8.4.0") == "onbekende browser"
    assert activity.browser_label(None) == "onbekende browser"


def _full_app_client(username: str, password: str) -> TestClient:
    """A signed-in browser on the whole app, next to the jobs-page fixture's own."""
    browser = TestClient(main.app)
    token = csrf_of(browser.get("/login").text)
    browser.post("/login", data={"username": username, "password": password, "_csrf": token}, follow_redirects=False)
    return browser


def _bootstrap_password(username: str) -> str:
    accounts = json.loads((config.RUNTIME_DIR / "bootstrap-accounts.json").read_text("utf-8"))["accounts"]
    return accounts[username]["password"]


def test_the_system_page_counts_sign_ins_and_the_account_page_tells_people(client):  # noqa: F811
    assert "90 dagen het moment, je adres en je browser" in client.get("/account").text
    failed = TestClient(main.app)
    token = csrf_of(failed.get("/login").text)
    failed.post("/login", data={"username": "test", "password": "fout", "_csrf": token}, follow_redirects=False)
    admin = _full_app_client("admin", _bootstrap_password("admin"))
    home = admin.get("/admin").text  # the fixture's own sign-in counts too
    assert "<dt>Keer ingelogd</dt><dd>2</dd>" in home and "<dt>Mislukte pogingen</dt><dd>1</dd>" in home


def test_events_are_in_the_export_and_gone_with_the_account(client):  # noqa: F811
    password = _bootstrap_password("test-user")
    member = _full_app_client("test-user", password)
    page = member.get("/account").text
    archive = member.post("/account/export", data={"_csrf": csrf_of(page)})
    assert archive.status_code == 200 and b"gegevens/auth_events.json" in archive.content
    with store.connect() as con:
        gone = con.execute("SELECT id FROM users WHERE username='test-user'").fetchone()["id"]
    assert store.list_auth_events(user_id=gone)
    member.post(
        "/account/verwijderen",
        data={"_csrf": csrf_of(page), "password": password, "confirm": "VERWIJDER"},
        follow_redirects=False,
    )
    assert store.list_auth_events(user_id=gone) == []


def test_an_inactive_account_and_a_rate_limited_attempt_are_failures_too(env, monkeypatch):  # noqa: F811
    from app.auth import routes as auth_routes

    admin = env.other_client()
    login(admin, "admin", env.password("admin"))
    target = user_id(env, "test-user")
    admin.post(f"/users/{target}/deactivate", data={"_csrf": csrf_of(admin.get("/users").text), "bevestig": "1"}, follow_redirects=False)
    login(env.client, "test-user", "wat-dan-ook")
    assert events(env, kind="login_failed")[-1]["user_id"] == target  # known account, still a failure
    monkeypatch.setattr(auth_routes, "_rate_limit_wait", lambda key: 30.0)
    assert login(env.client, "owner", "fout").status_code == 429
    last = events(env, kind="login_failed")[-1]
    assert last["username"] == "owner" and last["user_id"] is None  # refused before the lookup


def test_the_prune_runs_once_a_day_per_process(env, monkeypatch):  # noqa: F811
    from app import scheduler

    monkeypatch.setattr(scheduler, "_last_prune_day", None)
    assert scheduler.run_prune_due() is True
    assert scheduler.run_prune_due() is False
