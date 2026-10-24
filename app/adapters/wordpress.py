"""wordpress source adapter."""

from __future__ import annotations

import time

import httpx

from .. import store
from .common import (
    _skip_recent_detail,
    _tally,
    detail_get,
    make_job,
    page_text,
    split_company,
    strip_html,
)


def normalize_wp_item(source: dict, item: dict, client: httpx.Client | None = None) -> dict | None:
    title = strip_html(item.get("title"))
    url = item.get("link")
    if not title or not isinstance(url, str) or not url.startswith("http"):
        return None
    description = strip_html(item.get("content"))
    if not description and source.get("content_from_page") and client is not None:
        try:
            response = detail_get(client, url)
            if response.status_code < 400:
                description = page_text(response.text)
            time.sleep(0.4)
        except httpx.HTTPError:
            description = ""
    company, clean_title = split_company(title)
    return make_job(
        source["id"],
        url,
        clean_title,
        company=company,
        description=description,
        posted_at=str(item.get("date") or ""),
    )


def scan(client, source, entry, summary, limit_per_source=None) -> None:
    url = source["base_url"].rstrip("/") + source["path"]
    params = {"per_page": int(source.get("per_page", 30))}
    response = client.get(url, params=params)
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list):
        raise ValueError("unexpected payload (not a list)")
    limit = limit_per_source or len(items)
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        if _skip_recent_detail(str(item.get("link") or ""), entry, summary):
            continue
        job = normalize_wp_item(source, item, client)
        if not job:
            continue
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
    store.update_source_status(source["id"], "ok")


KINDS = ("wp-posts", "wp-job-listings")
