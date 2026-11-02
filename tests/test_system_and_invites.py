"""Operator views, source health, invitation links and document rewrites."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import auth, runtime, sources, store, tasks
from app.auth import invites
from app.main import app
from tests.test_jobs_page import add_job, client, soup  # noqa: F401
from tests.test_phases_3_5 import _letter


def _anonymous_form(path):
    browser = TestClient(app, base_url="https://testserver")
    page = browser.get(path)
    if page.status_code != 200:
        return browser, page, ""
    return browser, page, soup(page).select_one(f'input[name="{auth.CSRF_FIELD}"]')["value"]


def _submit(browser, path, csrf, **fields):
    return browser.post(
        path, data={auth.CSRF_FIELD: csrf, **fields}, follow_redirects=False
    )


# ---------------------------------------------------------------- invitations


def test_invitation_creates_one_normal_account_once(client):  # noqa: F811
    token = invites.create_invite()
    path = f"/uitnodiging/{token}"
    browser, page, csrf = _anonymous_form(path)
    assert page.status_code == 200
    response = _submit(browser, path, csrf, username="nieuwe.gebruiker", password="lang-genoeg-wachtwoord")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    with store.connect() as con:
        row = con.execute("SELECT role FROM users WHERE username='nieuwe.gebruiker'").fetchone()
    assert row["role"] == auth.ROLE_USER
    _again, page, _none = _anonymous_form(path)
    assert page.status_code == 404
    assert "verlopen of al gebruikt" in page.text
    assert _submit(browser, path, csrf, username="tweede.poging", password="lang-genoeg-wachtwoord").status_code == 404


def test_a_rejected_form_does_not_use_up_the_invitation(client):  # noqa: F811
    token = invites.create_invite()
    path = f"/uitnodiging/{token}"
    browser, _page, csrf = _anonymous_form(path)
    assert _submit(browser, path, csrf, username="ok.naam", password="kort").status_code == 400
    assert invites.invite_open(token)


def test_invitation_needs_the_session_token(client):  # noqa: F811
    token = invites.create_invite()
    browser, _page, _csrf = _anonymous_form(f"/uitnodiging/{token}")
    response = _submit(browser, f"/uitnodiging/{token}", "fout", username="ok.naam", password="lang-genoeg-wachtwoord")
    assert response.status_code == 403
    assert invites.invite_open(token)


def test_expired_and_unknown_invitations_are_refused(client):  # noqa: F811
    token = invites.create_invite()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with store.connect() as con:
        con.execute(
            "UPDATE meta SET value=? WHERE key=?", (json.dumps({"expires": past}), invites._key(token))
        )
    assert not invites.invite_open(token)
    assert not invites.consume_invite(token)
    assert not invites.invite_open("bestaat-niet")


def test_only_a_hash_of_the_invitation_is_stored(client):  # noqa: F811
    token = invites.create_invite()
    with store.connect() as con:
        rows = con.execute("SELECT key, value FROM meta WHERE key LIKE 'invite:%'").fetchall()
    assert rows and all(token not in row["key"] + row["value"] for row in rows)


# ---------------------------------------------------------------- source health


def _round(items, status="ok"):
    return {"finished_at": "2026-11-02", "sources": {"a": {"status": status, "items": items}}}


def test_a_sudden_drop_is_flagged_and_failures_do_not_change_normal(client):  # noqa: F811
    for _ in range(3):
        sources._record_health(_round(100))
    health = json.loads(store.meta_get("source_health"))["a"]
    assert health["dropped"] is False
    sources._record_health(_round(0, "error"))
    assert json.loads(store.meta_get("source_health"))["a"]["history"] == [100, 100, 100]
    sources._record_health(_round(10))
    health = json.loads(store.meta_get("source_health"))["a"]
    assert health["dropped"] is True
    assert health["history"] == [100, 100, 100, 10]


def test_history_stays_bounded(client):  # noqa: F811
    for _ in range(sources.HEALTH_ROUNDS + 5):
        sources._record_health(_round(5))
    assert len(json.loads(store.meta_get("source_health"))["a"]["history"]) == sources.HEALTH_ROUNDS


def test_full_text_share_is_per_source(client):  # noqa: F811
    for url, text in (("https://a.test/1", "x" * 600), ("https://a.test/2", "kort")):
        store.upsert_job(
            {"source_id": "s", "url": url, "title": "T", "description": text, "content_hash": url}
        )
    assert store.full_text_share_by_source()["s"] == 50


def test_job_seekers_do_not_see_source_failures(client):  # noqa: F811
    store.sync_sources([{"id": "kapot", "name": "Kapot", "kind": "rss", "enabled": True}])
    store.update_source_status("kapot", "error")
    text = client.get("/sources").text
    assert "Kapot" in text
    assert "mislukt" not in text


# ---------------------------------------------------------------- system view


def test_usage_overview_covers_all_accounts_without_naming_them(client):  # noqa: F811
    store.record_usage("s", "match", 10, 5, 0.5)
    with store.connect() as con:
        con.execute("UPDATE usage_log SET owner='ander'")
    store.record_usage("s", "match", 1, 1, 0.25)
    overview = store.usage_overview()
    assert overview["kinds"] == [{"kind": "match", "calls": 2, "tokens": 17, "cost": 0.75}]
    assert overview["days"][0]["calls"] == 2
    assert "owner" not in overview["days"][0]


def test_failure_overview_has_no_error_text_or_owner(client):  # noqa: F811
    task = tasks.enqueue("digest", owner=None, dedupe_key="x")
    tasks.claim_next()
    tasks.fail(task, "geheime tekst van iemand")
    rows = tasks.failures_overview()
    assert rows[0]["kind"] == "digest" and rows[0]["n"] == 1
    assert "geheime" not in json.dumps(rows)
    assert set(rows[0]) == {"kind", "n", "last"}


# ---------------------------------------------------------------- documents


def _prepare(monkeypatch):
    from app import docs

    monkeypatch.setattr(docs, "convert_pdf", lambda path: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(docs, "ensure_templates", lambda: None)
    monkeypatch.setattr(docs, "cv_identity", lambda: {"name": ["Test"], "contact": []})


def _fake_manager(monkeypatch, answer, prompts):
    def generate(prompt, **_):
        prompts.append(prompt)
        return answer, {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.01}

    monkeypatch.setattr(runtime, "manager", SimpleNamespace(client=object(), generate=generate))


def test_rewrite_makes_a_new_version_from_the_document_only(client, monkeypatch):  # noqa: F811
    _prepare(monkeypatch)
    job_id = add_job()
    _letter(job_id)
    document = store.list_documents(job_id)[0]
    prompts = []
    _fake_manager(monkeypatch, json.dumps({"paragraphs": ["Korte tekst."]}), prompts)
    response = client.post(
        f"/jobs/{job_id}/documents/{document['id']}/herschrijven",
        data={"style": "korter"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "doc_error" not in response.headers["location"]
    assert sorted(item["version"] for item in store.list_documents(job_id)) == [1, 2]
    assert "Eerste alinea." in prompts[0]
    newest = next(item for item in store.list_documents(job_id) if item["version"] == 2)
    page = client.get(f"/jobs/{job_id}/documents/{newest['id']}/verschil")
    html = soup(page)
    assert "Korte tekst." in html.select_one(".diff .add").get_text()
    assert "Eerste alinea." in html.select_one(".diff .del").get_text()


def test_a_bad_rewrite_keeps_the_document_and_explains(client, monkeypatch):  # noqa: F811
    _prepare(monkeypatch)
    job_id = add_job()
    _letter(job_id)
    document = store.list_documents(job_id)[0]
    _fake_manager(monkeypatch, "geen json", [])
    response = client.post(
        f"/jobs/{job_id}/documents/{document['id']}/herschrijven",
        data={"style": "formeler"},
        follow_redirects=False,
    )
    assert "doc_error" in response.headers["location"]
    assert len(store.list_documents(job_id)) == 1


def test_rewrite_without_ai_changes_nothing(client, monkeypatch):  # noqa: F811
    _prepare(monkeypatch)
    job_id = add_job()
    _letter(job_id)
    document = store.list_documents(job_id)[0]
    monkeypatch.setattr(runtime, "manager", None)
    response = client.post(
        f"/jobs/{job_id}/documents/{document['id']}/herschrijven",
        data={"style": "korter"},
        follow_redirects=False,
    )
    assert "doc_error" in response.headers["location"]
    assert len(store.list_documents(job_id)) == 1


def test_first_version_has_nothing_to_compare(client, monkeypatch):  # noqa: F811
    _prepare(monkeypatch)
    job_id = add_job()
    _letter(job_id)
    document = store.list_documents(job_id)[0]
    assert client.get(f"/jobs/{job_id}/documents/{document['id']}/verschil").status_code == 404


def test_static_files_revalidate_and_fonts_stay_cached(client):  # noqa: F811
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"
    assert "max-age" in client.get(
        "/static/fonts/schibsted-grotesk-latin-wght-normal.woff2"
    ).headers["cache-control"]
