"""The Account page fits the person looking at it: a job seeker gets the CV, mail and data
sections, an administrator who only administers gets sign-in matters alone."""

from __future__ import annotations

from test_auth import csrf_of, env, login, make_env  # noqa: F401

from app.auth import routes as auth_routes


def test_a_pure_administrator_sees_only_sign_in_matters(env):  # noqa: F811
    login(env.client, "admin", env.password("admin"))
    html = env.client.get("/account").text
    assert "de apparaten waarop je bent ingelogd" in html
    assert 'id="password-title"' in html and 'id="theme-title"' in html
    for seeker_only in ('id="cv-title"', 'id="digest-title"', 'id="data-title"', "Kies zelf welke vacaturesites"):
        assert seeker_only not in html
    assert "Ingelogd op" in html and "1 apparaat" in html


def test_a_job_seeker_keeps_the_cv_mail_and_data_sections(env):  # noqa: F811
    login(env.client, "test-user", env.password("test-user"))
    html = env.client.get("/account").text
    assert "het basis-CV waarop Jobcraft" in html
    for section in ('id="cv-title"', 'id="digest-title"', 'id="data-title"', 'id="password-title"'):
        assert section in html


def test_the_owner_keeps_everything_even_without_profile_files(env):  # noqa: F811
    login(env.client, "owner", "test-password")
    html = env.client.get("/account").text
    assert 'id="data-title"' in html and 'id="digest-title"' in html


def test_signing_out_everywhere_else_keeps_this_device(env):  # noqa: F811
    first = env.client
    login(first, "owner", "test-password")
    second = env.other_client()
    login(second, "owner", "test-password")
    html = first.get("/account").text
    assert "2 apparaten" in html and "/account/uitloggen-overal" in html
    answer = first.post("/account/uitloggen-overal", data={"_csrf": csrf_of(html)}, follow_redirects=False)
    assert answer.status_code == 303 and answer.headers["location"].endswith("uitgelogd=1")
    assert "Hier blijf je ingelogd" in first.get("/account?uitgelogd=1").text
    assert "1 apparaat" in first.get("/account").text
    assert second.get("/account", follow_redirects=False).status_code in (302, 303)
    from test_auth import _db

    with _db(env) as con:
        assert con.execute("SELECT kind FROM auth_events ORDER BY id DESC LIMIT 1").fetchone()["kind"] == "sessions_revoked"


def test_an_administrator_lands_on_systeem_only_without_a_profile(env, monkeypatch):  # noqa: F811
    admin = {"id": "x", "username": "a", "role": "admin"}
    monkeypatch.setattr(auth_routes.profile, "profile_exists", lambda: False)  # seeker() scopes then asks this
    assert auth_routes._default_home(admin) == "/admin"
    monkeypatch.setattr(auth_routes.profile, "profile_exists", lambda: True)
    assert auth_routes._default_home(admin) == "/"
    assert auth_routes._default_home({"id": "y", "username": "b", "role": "user"}) == "/"
