"""Gebruikers asks before it puts someone out, works as cards on a phone, and AI and Bronnen stand out where it matters."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from test_auth import _db, csrf_of, env, login, make_env, one_time_password, user_id  # noqa: F401
from test_auth_events import UA, _bootstrap_password, _full_app_client, events
from test_jobs_page import client  # noqa: F401

from app import config, model_routes


def _admin_on(env):  # noqa: F811
    admin = env.other_client()
    login(admin, "admin", env.password("admin"))
    return admin


def test_a_password_reset_asks_first_by_name_and_changes_nothing_until_confirmed(env):  # noqa: F811
    admin = _admin_on(env)
    target = user_id(env, "test-user")
    with _db(env) as con:
        before = con.execute("SELECT password_hash FROM users WHERE id=?", (target,)).fetchone()[0]
    page = admin.get("/users").text
    asked = admin.post(f"/users/{target}/reset-password", data={"_csrf": csrf_of(page)}, follow_redirects=False)
    assert asked.status_code == 200
    assert "Nieuw wachtwoord voor Testgebruiker?" in asked.text and "op alle apparaten uitgelogd" in asked.text
    assert 'name="bevestig" value="1"' in asked.text and 'href="/users">Annuleren</a>' in asked.text
    with _db(env) as con:
        assert con.execute("SELECT password_hash FROM users WHERE id=?", (target,)).fetchone()[0] == before
    assert events(env, user_id=target) == []
    done = admin.post(
        f"/users/{target}/reset-password", data={"_csrf": csrf_of(asked.text), "bevestig": "1"}, follow_redirects=False
    )
    assert done.status_code == 200 and one_time_password(done.text)
    assert [row["kind"] for row in events(env, user_id=target)] == ["password_reset"]


def test_deactivating_asks_first_and_the_confirm_button_is_the_dangerous_one(env):  # noqa: F811
    admin = _admin_on(env)
    target = user_id(env, "test-user")
    asked = admin.post(f"/users/{target}/deactivate", data={"_csrf": csrf_of(admin.get("/users").text)}, follow_redirects=False)
    assert asked.status_code == 200 and "Testgebruiker deactiveren?" in asked.text
    assert '<button class="button danger" type="submit">Ja, deactiveren</button>' in asked.text
    with _db(env) as con:
        assert con.execute("SELECT active FROM users WHERE id=?", (target,)).fetchone()[0] == 1


def test_an_administrator_can_sign_one_account_out_everywhere_and_the_log_names_them(env):  # noqa: F811
    member = env.other_client()
    member.headers["user-agent"] = UA
    assert login(member, "test-user", env.password("test-user")).status_code == 303
    admin = _admin_on(env)
    target = user_id(env, "test-user")
    with _db(env) as con:
        password_before = con.execute("SELECT password_hash FROM users WHERE id=?", (target,)).fetchone()[0]
    page = admin.get("/users").text
    assert "ingelogd op 1 apparaat" in page and "Safari op Mac" in page
    assert f'action="/users/{target}/uitloggen"' in page
    asked = admin.post(f"/users/{target}/uitloggen", data={"_csrf": csrf_of(page)}, follow_redirects=False)
    assert "Testgebruiker overal uitloggen?" in asked.text and "Het wachtwoord blijft hetzelfde" in asked.text
    assert member.get("/", follow_redirects=False).status_code == 200  # nothing happened yet
    done = admin.post(f"/users/{target}/uitloggen", data={"_csrf": csrf_of(asked.text), "bevestig": "1"}, follow_redirects=False)
    assert done.status_code == 200 and "test-user is overal uitgelogd" in done.text
    assert member.get("/", follow_redirects=False).status_code == 303
    with _db(env) as con:  # the same password: a lost phone, not a lost password
        assert con.execute("SELECT password_hash FROM users WHERE id=?", (target,)).fetchone()[0] == password_before
    rows = events(env, user_id=target, kind="sessions_revoked")
    assert len(rows) == 1 and rows[0]["actor_id"] == user_id(env, "admin")
    assert "Overal uitgelogd" in admin.get("/users/activiteit").text
    assert "nu nergens ingelogd" in done.text or "ingelogd op" in done.text
    assert admin.post("/users/onbekend/uitloggen", data={"_csrf": csrf_of(done.text), "bevestig": "1"}).status_code == 404
    # On their own account the device in hand stays signed in, as on the Account page.
    own = admin.post(f"/users/{user_id(env, 'admin')}/uitloggen", data={"_csrf": csrf_of(done.text), "bevestig": "1"}, follow_redirects=False)
    assert own.status_code == 200 and admin.get("/users", follow_redirects=False).status_code == 200


def test_each_account_has_a_menu_that_names_the_person_and_the_pages_share_the_tabs(env):  # noqa: F811
    admin = _admin_on(env)
    page = admin.get("/users").text
    assert 'aria-label="Acties voor Testgebruiker"' in page and 'class="table cards"' in page
    assert 'data-label="Laatst ingelogd"' in page and 'data-label="Apparaten"' in page
    assert "Je kunt je eigen account niet deactiveren." in page  # inside the admin's own menu
    assert page.count('aria-current="page">Accounts</a>') == 1 and 'href="/users/activiteit">Inlogactiviteit</a>' in page
    activity = admin.get("/users/activiteit").text
    assert 'aria-current="page">Inlogactiviteit</a>' in activity and "<h1>Gebruikers</h1>" in activity
    assert "Terug naar Gebruikers" not in activity
    assert "Nieuw wachtwoord" in page and 'href="/users/activiteit"' in page


def test_every_model_task_sits_in_exactly_one_stage():
    placed = [role for _label, roles in model_routes.STAGES for role in roles]
    assert sorted(placed) == sorted(role for role, _label, _about in model_routes.ROLES)
    assert len(placed) == len(set(placed))


def test_the_ai_page_groups_tasks_by_stage_and_another_accounts_call_shows_no_server_path(client):  # noqa: F811
    admin = _full_app_client("admin", _bootstrap_password("admin"))
    page = admin.get("/admin/ai").text
    first = page.index("Vacatures lezen en beoordelen")
    assert first < page.index("Sollicitatie schrijven") < page.index("Profiel</h3>")
    assert page.count('class="button quiet small"') == len(model_routes.ROLES)
    # A call logged by the legacy account, read by the administrator.
    directory = config.DATA_DIR / "ai-log"
    directory.mkdir(parents=True, exist_ok=True)
    day = f"{datetime.now(UTC):%Y-%m-%d}"
    row = {
        "at": datetime.now(UTC).isoformat(), "agent": "matcher", "model": "test-model", "ref": "",
        "input": [{"role": "user", "content": "geheim cv"}], "output": "oordeel",
        "input_tokens": 1, "output_tokens": 1, "duration_ms": 10, "cost_usd": 0.0, "error": "",
    }
    (directory / f"{day}.jsonl").write_text(json.dumps(row) + "\n", "utf-8")
    call = admin.get(f"/admin/ai/log/legacy/{day}/0")
    assert call.status_code == 200
    assert "geheim cv" not in call.text and ".jsonl" not in call.text and str(config.DATA_DIR) not in call.text
    assert "in het bestand van" in call.text
