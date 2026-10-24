"""exenzo source adapter."""
from __future__ import annotations

import time

from .. import store
from .common import _tally, make_job, strip_html


def normalize_exenzo(source: dict, item: dict) -> dict | None:
    title = strip_html(item.get("title"))
    slug = item.get("slug")
    public_site = source.get("public_site", "https://greenjobs.nl").rstrip("/")
    url = f"{public_site}/vacature/{slug}" if slug else ""
    if not url:
        url = item.get("job_url") or item.get("apply_url") or ""
    if not title or not isinstance(url, str) or not url:
        return None
    hours = ""
    hours_min, hours_max = item.get("hours_min"), item.get("hours_max")
    if hours_min or hours_max:
        hours = f"{hours_min or '?'}-{hours_max or '?'} uur"
    location = item.get("location_name") or item.get("city") or item.get("postal_code") or ""
    return make_job(
        source["id"],
        url,
        title,
        company=item.get("organisation_name") or "",
        location=str(location),
        hours=hours,
        description=strip_html(item.get("description")),
        posted_at=str(item.get("created_at") or ""),
        lat=item.get("lat"),
        lon=item.get("lon"),
    )



def _scan_exenzo(client, source, entry, summary, limit_per_source=None) -> None:
    base = source["base_url"].rstrip("/")
    path = source.get("path", "/api/public/v1/jobs")
    per_page = int(source.get("per_page", 100))
    max_pages = int(source.get("max_pages", 3))
    limit = limit_per_source or per_page * max_pages
    collected = 0
    for page_number in range(1, max_pages + 1):
        params: dict = {"limit": per_page, "page": page_number}
        if source.get("search"):
            params["search"] = source["search"]
        response = client.get(base + path, params=params)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("data") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            job = normalize_exenzo(source, item)
            if not job:
                continue
            _, created = store.upsert_job(job)
            _tally(entry, summary, created)
            collected += 1
            if collected >= limit:
                break
        if collected >= limit or len(items) < per_page:
            break
        time.sleep(0.5)
    store.update_source_status(source["id"], "ok")



scan = _scan_exenzo


KINDS = ('exenzo-json',)
