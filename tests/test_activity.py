"""Administrators see which parts of the app an account uses, never what was in them."""

from __future__ import annotations

import json

from app import activity, auth, store
from tests.test_jobs_page import add_job, client, soup  # noqa: F401
from tests.test_route_permissions import ADMIN, USER


def _log(client_) -> list[dict]:
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    return [json.loads(line) for path in sorted(activity._directory(owner).glob("*.jsonl")) for line in path.read_text().splitlines()]


def test_views_and_actions_are_logged_by_kind_without_their_content(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(activity, "_last", {})
    job_id = add_job("Geheime Vacature")
    before = len(_log(client))
    client.get("/jobs?q=zoekwoord")
    client.get(f"/jobs/{job_id}")
    client.post(f"/jobs/{job_id}/vote", data={"vote": "up"}, follow_redirects=False)
    rows = _log(client)[before:]
    assert [(row["part"], row["action"]) for row in rows] == [
        ("Vacatures", "Vacaturelijst bekeken"),
        ("Vacatures", "Vacature geopend"),
        ("Vacatures", "Duim gegeven"),
    ]
    text = json.dumps(rows)
    assert "zoekwoord" not in text and "Geheime" not in text and f"/jobs/{job_id}" not in text
    assert rows[1]["route"] == "/jobs/{job_id}" and rows[2]["method"] == "POST"


def test_a_page_that_reloads_itself_is_one_view_and_polls_are_none(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(activity, "_last", {})
    before = len(_log(client))
    for _ in range(4):
        client.get("/sollicitaties")
    client.get("/api/werk")
    client.get("/static/app.css")
    client.get("/account")
    client.get("/sollicitaties")  # back again after something else: a new view
    actions = [row["action"] for row in _log(client)[before:]]
    assert actions == ["Sollicitaties bekeken", "Account bekeken", "Sollicitaties bekeken"]


def test_every_named_action_is_a_real_route():
    assert set(activity.ACTIONS) <= ADMIN | USER  # the reviewed list of every endpoint
    assert activity.describe("POST", "/start/keuzes") == ("Snelle start", "Actie: /start/keuzes")


def test_old_days_are_removed(monkeypatch):
    monkeypatch.setenv("ACTIVITY_LOG_DAYS", "30")
    directory = activity._directory("u1")
    directory.mkdir(parents=True)
    (directory / "2020-01-01.jsonl").write_text("{}\n")
    activity.record("u1", "GET", "/jobs", "/jobs", 200, 5)
    assert [path.stem for path in directory.glob("*.jsonl")] != [] and not (directory / "2020-01-01.jsonl").exists()


def test_only_an_administrator_reads_it_and_sees_who_was_active(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(activity, "_last", {})
    client.get("/sollicitaties")
    assert client.get("/users/gebruik").status_code == 403
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
        con.execute("UPDATE users SET role=? WHERE id=?", (auth.ROLE_ADMIN, owner))
    page = soup(client.get("/users/gebruik?periode=dag"))
    text = page.get_text(" ", strip=True)
    assert "Sollicitaties bekeken" in text and "Laatst actief" in text
    assert page.select_one('a[href="/users/gebruik"][aria-current="page"]')
    only = soup(client.get("/users/gebruik?onderdeel=Account")).get_text(" ", strip=True)
    assert "Sollicitaties bekeken" not in only
