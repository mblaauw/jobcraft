"""Hardening from the security review: hosted defaults, unsized bodies, fetched addresses, links."""

from __future__ import annotations

import json

import httpx
import pytest
from test_jobs_page import add_job, client  # noqa: F401

from app import auth, config, job_import, main, store
from app.auth.credentials import verify_password


def test_a_hosted_site_without_app_password_gets_no_known_password(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "APP_USER", "owner")
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    monkeypatch.setenv("APP_COOKIE_SECURE", "1")
    auth.init_auth()
    with store.connect() as con:
        stored = con.execute("SELECT password_hash FROM users WHERE id='legacy'").fetchone()["password_hash"]
    handed_over = json.loads((tmp_path / "runtime" / "bootstrap-accounts.json").read_text())["accounts"]["owner"]
    assert not verify_password("dev-local", stored)
    assert verify_password(handed_over["password"], stored)


def test_a_form_without_a_length_is_read_only_up_to_the_limit(client):  # noqa: F811
    def endless():
        for _ in range(64):
            yield b"x" * 65536  # 4 MB in all, sent chunked

    response = client.post(
        "/jobs/toevoegen",
        content=endless(),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code in (400, 403, 413)


def test_a_small_form_without_a_length_still_arrives(client):  # noqa: F811
    from bs4 import BeautifulSoup

    token = BeautifulSoup(client.get("/jobs/toevoegen").text, "html.parser").select_one('input[name="_csrf"]')["value"]
    body = f"_csrf={token}&url=&text=".encode()
    response = client.post(
        "/jobs/toevoegen", content=iter([body]), headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code != 403  # the token was read: the form itself decides what comes next


class _Stream:
    def __init__(self, address):
        self.address = address

    def get_extra_info(self, name):
        return (self.address, 443) if name == "server_addr" else None


@pytest.mark.parametrize(("landed", "blocked"), [("127.0.0.1", True), ("10.0.0.5", True), ("93.184.216.34", False)])
def test_import_judges_the_address_it_actually_reached(monkeypatch, landed, blocked):
    monkeypatch.setattr(job_import.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 0))])

    def handler(request):
        return httpx.Response(200, text="<html>vacature</html>", extensions={"network_stream": _Stream(landed)})

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        if blocked:
            with pytest.raises(job_import.ImportBlocked):
                job_import._get(fake, "https://rebinding.test/job")
        else:
            assert job_import._get(fake, "https://rebinding.test/job").status_code == 200


def test_a_vacancy_link_that_is_not_a_web_address_is_not_shown(client):  # noqa: F811
    job_id = add_job("Rare bron")
    with store.connect() as con:
        con.execute("UPDATE jobs SET url='javascript:alert(1)' WHERE id=?", (job_id,))
    assert 'id="detail-original"' not in client.get(f"/jobs/{job_id}").text


def test_the_generated_api_pages_are_off(client):  # noqa: F811
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert main.app.openapi_url is None
