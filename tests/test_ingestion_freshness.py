import hashlib

import httpx

from app import store
from app.adapters import fondsen, rijksoverheid
from app.adapters.common import make_job


def test_empty_updates_preserve_rich_fields_and_rehash_merged_content():
    original = make_job(
        "fixture",
        "https://example.test/job",
        "Job",
        company="Company",
        location="Town",
        hours="24",
        description="Detailed role",
        posted_at="2026-10-20",
        lat=52,
        lon=5,
    )
    job_id, _ = store.upsert_job(original)
    empty = make_job("fixture", original["url"], "", description="")
    store.upsert_job(empty)
    row = store.get_job(job_id)
    for name in ("title", "company", "location", "hours", "description", "posted_at", "lat", "lon"):
        assert row[name] == original[name]
    assert row["content_hash"] == hashlib.sha256(b"Job:Detailed role").hexdigest()
    assert "detailed role" in row["search_text"]


def test_recent_known_detail_skip_does_not_roll_freshness_forever():
    source = {
        "id": "fondsen",
        "name": "Fondsen",
        "kind": "html-fondsen",
        "base_url": "https://example.test",
        "path": "/vacatures",
    }
    store.sync_sources([source])
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/vacatures":
            return httpx.Response(200, text='<a href="/vacature/test-role">Role</a>')
        return httpx.Response(200, text="<h1>Role</h1><p>Full vacancy details</p>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            entry, summary = {"new": 0, "updated": 0, "items": 0}, {"new": 0, "updated": 0}
            fondsen.scan(client, source, entry, summary)
        assert calls.count("/vacature/test-role") == 1
        assert entry["updated"] == entry["items"] == 1
        with store.connect() as con:
            con.execute(
                "UPDATE meta SET value='2000-01-01T00:00:00+00:00' WHERE key LIKE 'detail_fetched:%'"
            )
        fondsen.scan(client, source, entry, summary)
        assert calls.count("/vacature/test-role") == 2


def test_rijksoverheid_records_actual_detail_url_and_skips_repeat(monkeypatch):
    source = {
        "id": "rijk",
        "name": "Rijk",
        "kind": "hn-resource",
        "base_url": "https://example.test",
        "hn_ref": "fixture",
        "pages": 1,
        "per_page": 1,
    }
    store.sync_sources([source])
    monkeypatch.setattr(
        rijksoverheid,
        "_parse_hn_cards",
        lambda text: [{"href": "/job/1", "title": "Role", "employer": "Org", "labels": {}}],
    )
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text="<main><h1>Role</h1>Full public detail</main>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            rijksoverheid.scan(
                client, source, {"new": 0, "updated": 0, "items": 0}, {"new": 0, "updated": 0}
            )
    assert calls.count("/job/1") == 1
