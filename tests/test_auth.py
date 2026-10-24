"""Local auth contracts: tmp database, no LLM runtime, no personal data.

The tests mount ``auth.middleware`` and ``auth.router`` on a throwaway FastAPI app
so ``app.main`` (and the OpenCode runtime) stays out of the picture.
"""

from __future__ import annotations

import json
import re
import sqlite3
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from app import auth, config
from app.auth import rate_limit
from app.auth import routes as auth_routes


def _db(env) -> sqlite3.Connection:
    con = sqlite3.connect(env.root / "data" / "app.db")
    con.row_factory = sqlite3.Row
    return con


def csrf_of(html: str) -> str:
    match = re.search(r'name="_csrf" value="([^"]+)"', html)
    assert match, "no CSRF field in page"
    return match.group(1)


def login(client: TestClient, username: str, password: str, next_url: str = ""):
    token = csrf_of(client.get("/login").text)
    data = {"username": username, "password": password, "_csrf": token}
    if next_url:
        data["next"] = next_url
    return client.post("/login", data=data, follow_redirects=False)


def user_id(env, username: str) -> str:
    with _db(env) as con:
        return con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]


def one_time_password(html: str) -> str:
    match = re.search(r'<pre class="doc" tabindex="0">([^<]+)</pre>', html)
    assert match, "no one-time password on the page"
    return match.group(1).strip()


class Env:
    def __init__(self, app: FastAPI, root):
        self.app = app
        self.root = root
        self.client = TestClient(app)

    @property
    def bootstrap_path(self):
        return self.root / "runtime" / "bootstrap-accounts.json"

    def password(self, username: str) -> str:
        accounts = json.loads(self.bootstrap_path.read_text("utf-8"))["accounts"]
        return accounts[username]["password"]

    def other_client(self) -> TestClient:
        return TestClient(self.app)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(auth.middleware)
    app.include_router(auth.router)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        user = auth.require_user(request)
        return HTMLResponse(f"dashboard:{user['username']}:{user['role']}")

    @app.get("/api/ping")
    def api_ping(request: Request):
        auth.require_user(request)
        return JSONResponse({"ok": True})

    @app.post("/api/echo")
    def api_echo(request: Request):
        auth.require_user(request)
        return JSONResponse({"ok": True})

    @app.post("/api/form")
    def api_form(request: Request, text: str = Form("")):
        auth.require_user(request)
        return JSONResponse({"text": text})

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"ok": True})

    @app.post("/_test/last-admin-guard", response_class=HTMLResponse)
    def last_admin_guard(request: Request):
        # Exercises the "never remove the last active admin" branch, which the real
        # routes cannot reach while the acting admin is themselves active.
        request.state.user = {
            "id": "actor",
            "username": "actor",
            "display_name": "Actor",
            "role": auth.ROLE_ADMIN,
        }
        return auth.users_deactivate(request, "last-admin")

    return app


@pytest.fixture()
def make_env(tmp_path, monkeypatch):
    def factory(app_user="owner", app_password="test-password", suffix="default"):
        root = tmp_path / suffix
        monkeypatch.setattr(config, "DATA_DIR", root / "data")
        monkeypatch.setattr(config, "DB_PATH", root / "data" / "app.db")
        monkeypatch.setattr(config, "RUNTIME_DIR", root / "runtime")
        monkeypatch.setattr(config, "APP_USER", app_user)
        monkeypatch.setattr(config, "APP_PASSWORD", app_password)
        monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
        monkeypatch.setattr(rate_limit, "_failures", {})
        auth.init_auth()
        return Env(_build_app(), root)

    return factory


@pytest.fixture()
def env(make_env):
    return make_env()


# ---------------------------------------------------------------- bootstrap


def test_bootstrap_creates_private_secrets_and_is_idempotent(env):
    assert stat.S_IMODE(env.bootstrap_path.stat().st_mode) == 0o600
    admin_password = env.password("admin")
    member_password = env.password("test-user")
    assert admin_password and member_password and admin_password != member_password

    with _db(env) as con:
        legacy = con.execute("SELECT * FROM users WHERE id='legacy'").fetchone()
        admin = con.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        member = con.execute("SELECT * FROM users WHERE username='test-user'").fetchone()
        before = {
            row["username"]: row["password_hash"]
            for row in con.execute("SELECT * FROM users").fetchall()
        }

    assert legacy["username"] == "owner" and legacy["role"] == auth.ROLE_USER
    assert auth.verify_password("test-password", legacy["password_hash"])
    assert admin["role"] == auth.ROLE_ADMIN and member["role"] == auth.ROLE_USER
    assert str(uuid.UUID(admin["id"])) == admin["id"]
    assert str(uuid.UUID(member["id"])) == member["id"]
    for row in (legacy, admin, member):
        assert row["password_hash"].startswith("scrypt$")
        assert "test-password" not in row["password_hash"]

    auth.init_auth()  # rerun must not rotate anything
    with _db(env) as con:
        after = {
            row["username"]: row["password_hash"]
            for row in con.execute("SELECT * FROM users").fetchall()
        }
    assert after == before
    assert env.password("admin") == admin_password
    assert env.password("test-user") == member_password


def test_legacy_login_uses_explicit_dev_fallback_when_unconfigured(make_env):
    fallback = make_env(app_user="owner", app_password="", suffix="fallback")
    assert login(fallback.client, "owner", "dev-local").status_code == 303
    assert "dashboard:owner:user" in fallback.client.get("/").text


def test_legacy_username_follows_app_user(make_env):
    custom = make_env(app_user="d. staarink", app_password="geheim-wachtwoord", suffix="appuser")
    with _db(custom) as con:
        legacy = con.execute("SELECT * FROM users WHERE id='legacy'").fetchone()
    assert legacy["username"] == "d. staarink"
    assert login(custom.client, "d. staarink", "geheim-wachtwoord").status_code == 303


# ---------------------------------------------------------------- access control


def test_pages_redirect_and_api_is_401_without_session(env):
    page = env.client.get("/jobs?q=groen", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=%2Fjobs%3Fq%3Dgroen"
    assert env.client.get("/api/ping", follow_redirects=False).status_code == 401
    assert env.client.get("/healthz", follow_redirects=False).status_code == 200
    assert env.client.get("/static/app.css", follow_redirects=False).status_code == 404
    assert env.client.get("/", follow_redirects=False).status_code == 303


def test_login_page_issues_anonymous_session_and_csrf(env):
    response = env.client.get("/login", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Inloggen" in response.text
    assert "/static/app.css" in response.text
    raw = response.cookies.get(auth.COOKIE_NAME)
    assert raw
    token = csrf_of(response.text)
    assert len(token) >= 20
    with _db(env) as con:
        rows = con.execute("SELECT token_hash, user_id, csrf_token FROM auth_sessions").fetchall()
    assert [row["user_id"] for row in rows] == [None]
    assert rows[0]["token_hash"] != raw
    assert rows[0]["token_hash"] == auth._hash_token(raw)

    authenticated = env.client.get("/login?next=/jobs")
    assert authenticated.status_code == 200  # still anonymous, so the form renders


def test_login_page_redirects_authenticated_users(env):
    assert login(env.client, "owner", "test-password").status_code == 303
    response = env.client.get("/login", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    with_next = env.client.get("/login", params={"next": "/jobs"}, follow_redirects=False)
    assert with_next.headers["location"] == "/jobs"


@pytest.mark.parametrize("cookie_state", ["invalid", "expired", "revoked"])
def test_login_replaces_stale_cookie_and_succeeds_first_time(env, cookie_state):
    if cookie_state == "invalid":
        env.client.cookies.set(auth.COOKIE_NAME, "invalid-token")
    else:
        env.client.get("/login")
        with _db(env) as con:
            if cookie_state == "expired":
                con.execute("UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00+00:00'")
            else:
                con.execute("UPDATE auth_sessions SET revoked=1")
    response = env.client.get("/login")
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1 and "Max-Age=0" not in cookies[0]
    response = env.client.post(
        "/login",
        data={"username": "owner", "password": "test-password", "_csrf": csrf_of(response.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "dashboard:owner:user" in env.client.get("/").text


def test_login_post_without_or_with_wrong_csrf_never_checks_credentials(env):
    env.client.get("/login")
    missing = env.client.post(
        "/login", data={"username": "owner", "password": "test-password"}, follow_redirects=False
    )
    assert missing.status_code == 403
    wrong = env.client.post(
        "/login",
        data={"username": "owner", "password": "test-password", "_csrf": "niet-de-token"},
        follow_redirects=False,
    )
    assert wrong.status_code == 403
    assert env.client.get("/", follow_redirects=False).status_code == 303
    with _db(env) as con:
        assert con.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked=0").fetchone()[0] == 1


def test_successful_login_sets_session_and_honours_local_next(env):
    response = login(env.client, "owner", "test-password", next_url="/jobs?q=groen")
    assert response.status_code == 303 and response.headers["location"] == "/jobs?q=groen"
    cookie_header = response.headers["set-cookie"]
    assert f"{auth.COOKIE_NAME}=" in cookie_header
    assert (
        "HttpOnly" in cookie_header
        and "SameSite=lax" in cookie_header
        and "Path=/" in cookie_header
    )
    assert "Secure" not in cookie_header
    assert "dashboard:owner:user" in env.client.get("/").text
    assert env.client.get("/account").status_code == 200
    assert env.client.get("/account").headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "target",
    [
        "//evil.test",
        "https://evil.test/x",
        "/login",
        "/logout",
        "\\evil.test",
        "/jobs\nInjected",
        " /jobs ",
    ],
)
def test_login_refuses_unsafe_next(env, target):
    response = login(env.client, "owner", "test-password", next_url=target)
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_admin_login_reaches_users_page_and_member_does_not(env):
    assert login(env.client, "admin", env.password("admin")).status_code == 303
    page = env.client.get("/users")
    assert page.status_code == 200
    assert "test-user" in page.text and "owner" in page.text
    assert "scrypt$" not in page.text and "password_hash" not in page.text
    assert env.password("test-user") not in page.text
    assert "Eenmalig wachtwoord" not in page.text


def test_member_cannot_reach_admin_page(env):
    assert login(env.client, "owner", "test-password").status_code == 303
    assert env.client.get("/users", follow_redirects=False).status_code == 403


# ---------------------------------------------------------------- sessions


def test_wrong_credentials_are_generic_and_rotate_the_form_token(env):
    token = csrf_of(env.client.get("/login").text)
    response = env.client.post(
        "/login",
        data={"username": "owner", "password": "fout", "_csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "Onjuiste gebruikersnaam of wachtwoord." in response.text
    assert csrf_of(response.text) != token
    assert env.client.get("/", follow_redirects=False).status_code == 303

    unknown = env.client.post(
        "/login",
        data={"username": "bestaat-niet", "password": "fout", "_csrf": csrf_of(response.text)},
        follow_redirects=False,
    )
    assert unknown.status_code == 401
    assert "Onjuiste gebruikersnaam of wachtwoord." in unknown.text


def test_login_failures_are_rate_limited_with_an_expiring_window(env, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(rate_limit, "_clock", lambda: clock["now"])
    token = csrf_of(env.client.get("/login").text)
    statuses = []
    for _ in range(auth._FAILURE_MAX):
        response = env.client.post(
            "/login",
            data={"username": "owner", "password": "fout", "_csrf": token},
            follow_redirects=False,
        )
        statuses.append(response.status_code)
        token = csrf_of(response.text)
    assert statuses == [401] * auth._FAILURE_MAX
    blocked = env.client.post(
        "/login",
        data={"username": "owner", "password": "fout", "_csrf": token},
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    even_correct = env.client.post(
        "/login",
        data={"username": "owner", "password": "test-password", "_csrf": csrf_of(blocked.text)},
        follow_redirects=False,
    )
    assert even_correct.status_code == 429

    clock["now"] += auth._FAILURE_WINDOW_SECONDS + 1  # window expires: no endless lockout
    assert auth._rate_limit_wait("owner") == 0.0
    recovered = env.client.post(
        "/login",
        data={"username": "owner", "password": "test-password", "_csrf": csrf_of(blocked.text)},
        follow_redirects=False,
    )
    assert recovered.status_code == 303


def test_successful_login_clears_recorded_failures(env):
    token = csrf_of(env.client.get("/login").text)
    env.client.post(
        "/login",
        data={"username": "owner", "password": "fout", "_csrf": token},
        follow_redirects=False,
    )
    assert rate_limit._failures
    login(env.client, "owner", "test-password")
    assert rate_limit._failures == {}


def test_non_ascii_csrf_fails_closed_without_a_server_error(env):
    env.client.get("/login")
    response = env.client.post(
        "/login",
        data={"username": "owner", "password": "test-password", "_csrf": "café"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    login(env.client, "owner", "test-password")
    assert env.client.post("/logout", data={"_csrf": "ééé"}).status_code == 403


@pytest.mark.parametrize("username", ["admin", "Admin", "TEST-USER"])
def test_bootstrap_reserved_username_is_rejected(make_env, username):
    with pytest.raises(RuntimeError, match="reserved"):
        make_env(app_user=username)


def test_bootstrap_password_is_consumed_on_first_login_and_never_reissued(env):
    original = env.password("admin")
    assert login(env.client, "admin", original).status_code == 303
    assert env.password("admin") is None
    auth.init_auth()
    assert env.password("admin") is None
    with _db(env) as con:
        con.execute("DELETE FROM users WHERE username='admin'")
    auth.init_auth()
    assert env.password("admin") != original


def test_csrf_is_required_for_every_unsafe_method(env):
    assert login(env.client, "owner", "test-password").status_code == 303
    token = csrf_of(env.client.get("/account").text)

    assert env.client.post("/logout", data={}, follow_redirects=False).status_code == 403
    assert env.client.get("/", follow_redirects=False).status_code == 200

    gone = env.client.post("/logout", data={"_csrf": token}, follow_redirects=False)
    assert gone.status_code == 303 and gone.headers["location"] == "/login"
    assert env.client.get("/", follow_redirects=False).status_code == 303
    with _db(env) as con:
        assert con.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked=1").fetchone()[0] >= 1


def test_header_token_covers_json_and_form_bodies(env):
    assert login(env.client, "owner", "test-password").status_code == 303
    token = csrf_of(env.client.get("/account").text)

    rejected = env.client.post("/api/echo", json={"text": "hoi"})
    assert rejected.status_code == 403 and rejected.json()["detail"]

    accepted = env.client.post("/api/echo", json={"text": "hoi"}, headers={auth.CSRF_HEADER: token})
    assert accepted.status_code == 200 and accepted.json() == {"ok": True}

    form = env.client.post("/api/form", data={"text": "hallo", "_csrf": token})
    assert form.status_code == 200 and form.json() == {"text": "hallo"}

    assert env.client.post("/api/form", data={"_csrf": "fout", "text": "x"}).status_code == 403


def test_expired_session_is_deleted_and_cookie_cleared(env):
    login(env.client, "owner", "test-password")
    raw = env.client.cookies.get(auth.COOKIE_NAME)
    token_hash = auth._hash_token(raw)
    with _db(env) as con, con:
        con.execute(
            "UPDATE auth_sessions SET expires_at=? WHERE token_hash=?",
            ("2000-01-01T00:00:00+00:00", token_hash),
        )
    response = env.client.get("/", follow_redirects=False)
    assert response.status_code == 303
    set_cookie = response.headers.get("set-cookie", "")
    assert auth.COOKIE_NAME in set_cookie and "Max-Age=0" in set_cookie
    with _db(env) as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE token_hash=?", (token_hash,)
            ).fetchone()[0]
            == 0
        )


def test_cookie_secure_flag_is_opt_in(make_env, monkeypatch):
    monkeypatch.setattr(config, "APP_COOKIE_SECURE", True, raising=False)
    secure = make_env(suffix="secure")
    header = secure.client.get("/login", follow_redirects=False).headers["set-cookie"]
    assert "Secure" in header


def test_deactivated_user_loses_existing_sessions(env):
    member_password = env.password("test-user")
    member = env.other_client()
    assert login(member, "test-user", member_password).status_code == 303
    assert member.get("/", follow_redirects=False).status_code == 200

    assert login(env.client, "admin", env.password("admin")).status_code == 303
    token = csrf_of(env.client.get("/users").text)
    response = env.client.post(
        f"/users/{user_id(env, 'test-user')}/deactivate",
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 200 and "gedeactiveerd" in response.text
    assert member.get("/", follow_redirects=False).status_code == 303
    with _db(env) as con:
        assert con.execute("SELECT active FROM users WHERE username='test-user'").fetchone()[0] == 0

    token = csrf_of(response.text)
    reactivated = env.client.post(
        f"/users/{user_id(env, 'test-user')}/activate",
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert reactivated.status_code == 200 and "weer actief" in reactivated.text
    assert login(env.other_client(), "test-user", member_password).status_code == 303


# ---------------------------------------------------------------- account


def test_password_change_checks_input_and_revokes_other_sessions(env):
    assert login(env.client, "owner", "test-password").status_code == 303
    other = env.other_client()
    assert login(other, "owner", "test-password").status_code == 303

    token = csrf_of(env.client.get("/account").text)
    wrong = env.client.post(
        "/account/password",
        data={
            "_csrf": token,
            "current_password": "fout",
            "new_password": "nieuw-wachtwoord-1",
            "confirm_password": "nieuw-wachtwoord-1",
        },
        follow_redirects=False,
    )
    assert wrong.status_code == 400 and "huidige wachtwoord" in wrong.text

    short = env.client.post(
        "/account/password",
        data={
            "_csrf": csrf_of(wrong.text),
            "current_password": "test-password",
            "new_password": "kort",
            "confirm_password": "kort",
        },
        follow_redirects=False,
    )
    assert short.status_code == 400 and "minstens 10 tekens" in short.text

    mismatch = env.client.post(
        "/account/password",
        data={
            "_csrf": csrf_of(short.text),
            "current_password": "test-password",
            "new_password": "nieuw-wachtwoord-1",
            "confirm_password": "nieuw-wachtwoord-2",
        },
        follow_redirects=False,
    )
    assert mismatch.status_code == 400 and "niet gelijk" in mismatch.text

    reuse = env.client.post(
        "/account/password",
        data={
            "_csrf": csrf_of(mismatch.text),
            "current_password": "test-password",
            "new_password": "test-password",
            "confirm_password": "test-password",
        },
        follow_redirects=False,
    )
    assert reuse.status_code == 400 and "nog niet gebruikte" in reuse.text

    changed = env.client.post(
        "/account/password",
        data={
            "_csrf": csrf_of(reuse.text),
            "current_password": "test-password",
            "new_password": "nieuw-wachtwoord-1",
            "confirm_password": "nieuw-wachtwoord-1",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303 and changed.headers["location"] == "/account?changed=1"
    assert env.client.get("/", follow_redirects=False).status_code == 200
    assert other.get("/", follow_redirects=False).status_code == 303

    with _db(env) as con:
        stored = con.execute("SELECT password_hash FROM users WHERE id='legacy'").fetchone()[0]
    assert auth.verify_password("nieuw-wachtwoord-1", stored)
    assert not auth.verify_password("test-password", stored)
    assert login(env.other_client(), "owner", "test-password").status_code == 401
    assert login(env.other_client(), "owner", "nieuw-wachtwoord-1").status_code == 303


# ---------------------------------------------------------------- admin users


def test_admin_creates_user_with_one_time_password(env):
    assert login(env.client, "admin", env.password("admin")).status_code == 303
    token = csrf_of(env.client.get("/users").text)
    created = env.client.post(
        "/users",
        data={"_csrf": token, "username": "collega", "display_name": "Collega", "role": "user"},
        follow_redirects=False,
    )
    assert created.status_code == 200
    generated = one_time_password(created.text)
    assert len(generated) >= 16
    new_id = user_id(env, "collega")
    assert str(uuid.UUID(new_id)) == new_id

    with _db(env) as con:
        row = con.execute("SELECT * FROM users WHERE username='collega'").fetchone()
    assert generated not in row["password_hash"] and row["password_hash"].startswith("scrypt$")
    assert row["role"] == auth.ROLE_USER and row["active"] == 1
    assert login(env.other_client(), "collega", generated).status_code == 303

    # The one-time password is not echoed on a normal page load.
    assert generated not in env.client.get("/users").text


def test_admin_users_validation_and_guards(env):
    assert login(env.client, "admin", env.password("admin")).status_code == 303
    token = csrf_of(env.client.get("/users").text)

    duplicate = env.client.post(
        "/users",
        data={"_csrf": token, "username": "test-user", "role": "user"},
        follow_redirects=False,
    )
    assert duplicate.status_code == 400 and "bestaat al" in duplicate.text

    bad_username = env.client.post(
        "/users", data={"_csrf": token, "username": "a", "role": "user"}, follow_redirects=False
    )
    assert bad_username.status_code == 400

    bad_role = env.client.post(
        "/users",
        data={"_csrf": token, "username": "nieuweling", "role": "superuser"},
        follow_redirects=False,
    )
    assert bad_role.status_code == 400 and "Onbekende rol" in bad_role.text

    short = env.client.post(
        "/users",
        data={"_csrf": token, "username": "nieuweling", "role": "user", "password": "kort"},
        follow_redirects=False,
    )
    assert short.status_code == 400

    assert (
        env.client.post(
            f"/users/{user_id(env, 'admin')}/deactivate", data={"_csrf": token}
        ).status_code
        == 400
    )

    unknown = env.client.post("/users/onbekend/reset-password", data={"_csrf": token})
    assert unknown.status_code == 404


def test_admin_can_reset_a_password_once(env):
    assert login(env.client, "admin", env.password("admin")).status_code == 303
    member_id = user_id(env, "test-user")
    member = env.other_client()
    assert login(member, "test-user", env.password("test-user")).status_code == 303

    token = csrf_of(env.client.get("/users").text)
    reset = env.client.post(
        f"/users/{member_id}/reset-password", data={"_csrf": token}, follow_redirects=False
    )
    assert reset.status_code == 200
    new_password = one_time_password(reset.text)
    assert new_password != env.password("test-user")
    assert member.get("/", follow_redirects=False).status_code == 303
    assert login(env.other_client(), "test-user", new_password).status_code == 303
    assert login(env.other_client(), "test-user", env.password("test-user")).status_code == 401


def test_concurrent_admin_deactivation_cannot_remove_every_admin(env):
    with _db(env) as con, con:
        con.execute(
            "INSERT INTO users (id, username, display_name, role, password_hash, created_at, active)"
            " VALUES (?,?,?,?,?,?,1)",
            (
                "admin-second",
                "admin-second",
                "Tweede beheerder",
                auth.ROLE_ADMIN,
                auth.hash_password("wachtwoord-123"),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        first = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
    barrier = Barrier(2)

    def attempt(target):
        barrier.wait(timeout=10)
        return auth._deactivate_without_losing_last_admin(target)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, [first, "admin-second"]))
    assert sorted(outcomes, key=str) == [None, "last_admin"]
    with _db(env) as con:
        active_admins = con.execute(
            "SELECT COUNT(*) FROM users WHERE role=? AND active=1", (auth.ROLE_ADMIN,)
        ).fetchone()[0]
    assert active_admins == 1


def test_last_active_admin_branch_is_refused(env, monkeypatch):
    with _db(env) as con, con:
        con.execute(
            "INSERT INTO users (id, username, display_name, role, password_hash, created_at, active)"
            " VALUES ('last-admin','laatste','Laatste',?,?,?,1)",
            (auth.ROLE_ADMIN, auth.hash_password("wachtwoord-123"), "2026-01-01T00:00:00+00:00"),
        )
    assert login(env.client, "admin", env.password("admin")).status_code == 303
    monkeypatch.setattr(
        auth_routes, "_deactivate_without_losing_last_admin", lambda _: "last_admin"
    )
    token = csrf_of(env.client.get("/users").text)
    response = env.client.post("/_test/last-admin-guard", data={"_csrf": token})
    assert response.status_code == 400
    assert "actieve beheerder" in response.text
    with _db(env) as con:
        assert con.execute("SELECT active FROM users WHERE id='last-admin'").fetchone()[0] == 1


# ---------------------------------------------------------------- primitives


def test_password_hashing_is_salted_stdlib_scrypt():
    first = auth.hash_password("geheim-wachtwoord")
    second = auth.hash_password("geheim-wachtwoord")
    assert first.startswith("scrypt$16384$8$1$") and first != second
    assert auth.verify_password("geheim-wachtwoord", first)
    assert not auth.verify_password("ander-wachtwoord", first)
    assert not auth.verify_password("geheim-wachtwoord", None)
    assert not auth.verify_password("", first)
    assert not auth.verify_password("geheim-wachtwoord", "bcrypt$wat-dan-ook")
    tampered = first.replace("16384", "99999999", 1)
    assert not auth.verify_password("geheim-wachtwoord", tampered)


def test_safe_next_whitelists_local_paths():
    assert auth.safe_next("/jobs?q=1") == "/jobs?q=1"
    assert auth.safe_next("") == ""
    assert auth.safe_next(None) == ""
    for unsafe in (
        "//evil.test",
        "https://evil.test",
        "/login",
        "/logout",
        "\\x",
        "/jobs\nInjected",
    ):
        assert auth.safe_next(unsafe) == ""
