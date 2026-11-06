"""exenzo source adapter.

The public API cuts descriptions off at 300 characters. With `enrich_details` the
board's public job page (JobPosting JSON-LD) fills in the text: at most `detail_cap`
pages per round, and a page read before is not read again for two weeks.
"""
from __future__ import annotations

import time

from .. import store
from .common import SHORT_SUMMARY, _tally, best_description, make_job, safe_detail_get, strip_html
from .jsonld import jsonld_jobs

DETAIL_DAYS = 14


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



def _full_text(client, url: str, entry: dict) -> str | None:
    page = safe_detail_get(client, url, entry)
    time.sleep(0.4)
    if page is None or page.status_code >= 400:
        return None
    found = jsonld_jobs(page.text)
    text = best_description(strip_html(found[0].get("description")) if found else "", page.text)
    return text if len(text) >= SHORT_SUMMARY else None


def _scan_exenzo(client, source, entry, summary, limit_per_source=None) -> None:
    base = source["base_url"].rstrip("/")
    path = source.get("path", "/api/public/v1/jobs")
    per_page = int(source.get("per_page", 100))
    max_pages = int(source.get("max_pages", 3))
    limit = limit_per_source or per_page * max_pages
    collected = 0
    budget = int(source.get("detail_cap", 60)) if source.get("enrich_details") else 0
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
            if source.get("enrich_details") and len(job["description"]) < SHORT_SUMMARY:
                if store.touch_recent_detail(job["url"], DETAIL_DAYS * 86400):
                    _tally(entry, summary, False)  # read in full not long ago; keep that text
                    collected += 1
                    continue
                if budget > 0:
                    budget -= 1
                    full = _full_text(client, job["url"], entry)
                    if full:
                        job = make_job(**{**_job_args(job), "description": full})
            _, created = store.upsert_job(job)
            _tally(entry, summary, created)
            collected += 1
            if collected >= limit:
                break
        if collected >= limit or len(items) < per_page:
            break
        time.sleep(0.5)
    store.update_source_status(source["id"], "ok")



def _job_args(job: dict) -> dict:
    keys = ("source_id", "url", "title", "company", "location", "hours", "description", "posted_at", "lat", "lon")
    return {key: job[key] for key in keys}


scan = _scan_exenzo


KINDS = ('exenzo-json',)
