import importlib

import httpx

from app import adapters, sources, store
from app.adapters import wordpress


def test_registry_includes_all_implemented_aliases():
    registry = adapters.registry()
    assert set(registry) == {
        "wp-posts",
        "wp-job-listings",
        "html-fondsen",
        "html-listing",
        "sitemap-jsonld",
        "rss",
        "exenzo-json",
        "defensie-cms",
        "hn-resource",
        "local-json",
    }
    assert registry["wp-posts"] is registry["wp-job-listings"]


def test_wordpress_registry_adapter_preserves_counts_and_storage():
    source = {
        "id": "wp",
        "name": "WP",
        "kind": "wp-posts",
        "base_url": "https://example.test",
        "path": "/posts",
    }
    store.sync_sources([source])
    payload = [
        {
            "title": {"rendered": "A job"},
            "content": {"rendered": "Full description"},
            "link": "https://example.test/jobs/1",
        }
    ]
    entry, summary = {"new": 0, "updated": 0, "items": 0}, {"new": 0, "updated": 0}
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        wordpress.scan(client, source, entry, summary)
    assert entry == {"new": 1, "updated": 0, "items": 1}
    assert summary == {"new": 1, "updated": 0}
    assert store.list_jobs()[0]["description"] == "Full description"


def test_new_adapter_needs_only_one_module(tmp_path, monkeypatch):
    (tmp_path / "synthetic_adapter.py").write_text(
        "KINDS=('synthetic-kind',)\ndef scan(*args): return None\n"
    )
    monkeypatch.setattr(adapters, "__path__", [*adapters.__path__, str(tmp_path)])
    importlib.invalidate_caches()
    adapters.registry.cache_clear()
    try:
        assert callable(adapters.registry()["synthetic-kind"])
    finally:
        adapters.registry.cache_clear()


def test_runner_uses_registry_and_preserves_unsupported_skip(monkeypatch):
    monkeypatch.setattr(sources, "load_sources", lambda: [{"id": "unsupported", "kind": "missing"}])
    summary = sources.scan()
    assert summary["sources"]["unsupported"]["status"] == "skipped"
    assert summary["new"] == summary["updated"] == 0
