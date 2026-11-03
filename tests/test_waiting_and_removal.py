"""Removing old versions, and work that shows it is running where it was started."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from test_jobs_page import add_job, client  # noqa: F401
from test_studio import CV, _no_pdf, _plan

from app import config, document_pipeline, store, tenancy


def _page(client, url):  # noqa: F811
    return BeautifulSoup(client.get(url).text, "html.parser")


def test_removing_a_version_deletes_its_files_and_keeps_the_others(client, monkeypatch):  # noqa: F811
    _no_pdf(monkeypatch)
    job_id = add_job()
    _plan(job_id)
    job = dict(store.get_job(job_id))
    first = document_pipeline.save_version(job, "resume", CV)
    second = document_pipeline.save_version(job, "resume", CV)
    path = config.DATA_DIR / store.get_document(first["id"])["docx_path"]
    preview = path.with_name(path.stem + "-p1.png")
    preview.write_bytes(b"png")
    assert path.exists() and path.with_suffix(".json").exists()
    answer = client.post(
        f"/jobs/{job_id}/documents/{first['id']}/verwijderen",
        data={"next": f"/jobs/{job_id}/sollicitatie"},
        follow_redirects=False,
    )
    assert answer.status_code == 303 and "verwijderd=1" in answer.headers["location"]
    assert [row["id"] for row in store.list_documents(job_id)] == [second["id"]]
    assert not path.exists() and not path.with_suffix(".json").exists() and not preview.exists()
    assert (config.DATA_DIR / store.get_document(second["id"])["docx_path"]).exists()
    assert "Die versie is verwijderd" in client.get(answer.headers["location"]).text


def test_an_old_document_can_be_removed_from_the_job_page(client):  # noqa: F811
    job_id = add_job()
    document_id = store.add_document(job_id, "motivatie", 1, "jobs/x/motivatie-v1.docx", None, "{}")
    page = _page(client, f"/jobs/{job_id}")
    assert page.select_one(f'form[action="/jobs/{job_id}/documents/{document_id}/verwijderen"]')
    answer = client.post(f"/jobs/{job_id}/documents/{document_id}/verwijderen", follow_redirects=False)
    assert answer.headers["location"] == f"/jobs/{job_id}?verwijderd=1#documenten"
    assert store.list_documents(job_id) == []


def test_nobody_removes_a_document_of_another_account(client):  # noqa: F811
    job_id = add_job()
    with tenancy.scope({"id": "someone-else"}):
        other = store.add_document(job_id, "resume", 1, "jobs/x/resume-v1.docx", None, "{}")
    answer = client.post(f"/jobs/{job_id}/documents/{other}/verwijderen")
    assert answer.status_code == 404
    with tenancy.scope({"id": "someone-else"}):
        assert store.get_document(other)


def test_the_apply_button_says_what_happens_next(client, monkeypatch):  # noqa: F811
    from app import profile

    _no_pdf(monkeypatch)
    profile.write_profile("# Profiel\n\n## Werkervaring\nAdviseur\n", "test")
    job_id = add_job()
    first = _page(client, f"/jobs/{job_id}")
    assert first.select_one(".apply-steps") and first.select_one("#detail-apply").get_text(strip=True) == "Maak je sollicitatie"
    now = datetime.now(UTC).isoformat()
    store.meta_set(f"doc_status:{job_id}", json.dumps({"state": "running", "kind": "plan", "started_at": now, "updated_at": now}))
    running = _page(client, f"/jobs/{job_id}")
    wait = running.select_one(".job-documents .wait")
    assert wait and "We maken je plan" in wait.get_text() and wait.select_one(f'[data-elapsed="{now}"]')
    store.meta_set(f"doc_status:{job_id}", json.dumps({"state": "done", "kind": "plan"}))
    _plan(job_id)
    planned = _page(client, f"/jobs/{job_id}")
    assert "Kies je verhaal" in planned.select_one("#detail-apply").get_text()
    document_pipeline.save_version(dict(store.get_job(job_id)), "resume", CV)
    made = _page(client, f"/jobs/{job_id}")
    assert made.select_one("#detail-apply").get_text(strip=True) == "Open je sollicitatie"


def test_a_silent_task_stops_showing_as_running(client):  # noqa: F811
    job_id = add_job()
    old = "2020-01-01T00:00:00+00:00"
    store.meta_set(f"doc_status:{job_id}", json.dumps({"state": "running", "kind": "both", "updated_at": old}))
    assert not _page(client, f"/jobs/{job_id}").select_one(".job-documents .wait")


def test_the_studio_progress_shows_how_long_it_runs(client):  # noqa: F811
    from app import profile

    profile.write_profile("# Profiel\n\n## Werkervaring\nAdviseur\n", "test")
    job_id = add_job()
    now = datetime.now(UTC).isoformat()
    store.meta_set(f"doc_status:{job_id}", json.dumps({"state": "running", "kind": "both", "started_at": now, "updated_at": now}))
    assert _page(client, f"/jobs/{job_id}/sollicitatie").select_one(f'[data-elapsed="{now}"]')
