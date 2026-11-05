"""Getro job boards (DOEN Impact Jobs): server-rendered Next.js pages, no API.

Getro's search API is closed to crawlers by its robots.txt, so the board's own pages
are all there is. The list page carries the newest jobs in its __NEXT_DATA__; each
job page carries the whole vacancy (text, places, status). Jobs that dropped off the
list are read again from their own page while they stay active, so a live vacancy
never looks expired; a closed one simply stops being seen.
"""

from __future__ import annotations

import json
import re
import time

from .. import store
from .ats import in_netherlands
from .common import (
    SHORT_SUMMARY,
    _skip_recent_detail,
    _tally,
    best_description,
    make_job,
    safe_detail_get,
    strip_html,
)

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
PAGE_CAP = 40


def next_state(raw: str) -> dict:
    match = _NEXT_DATA.search(raw)
    if not match:
        raise ValueError("Geen __NEXT_DATA__ op de pagina: de opbouw van het bord is veranderd")
    return ((json.loads(match.group(1)).get("props") or {}).get("pageProps") or {}).get("initialState") or {}


def _job_url(base: str, job: dict) -> str:
    company = (job.get("organization") or {}).get("slug")
    return f"{base}/companies/{company}/jobs/{job['slug']}" if company and job.get("slug") else ""


def _place(locations: list) -> str:
    names = [str(item.get("name") if isinstance(item, dict) else item or "") for item in locations or []]
    dutch = [name for name in names if in_netherlands(name)]
    return re.sub(r",\s*(?:the )?netherlands$", "", dutch[0], flags=re.I).strip() if dutch else ""


def _scan_getro(client, source, entry, summary, limit_per_source=None) -> None:
    base = source["base_url"].rstrip("/")
    response = client.get(base + source.get("path", "/jobs"))
    response.raise_for_status()
    jobs = (next_state(response.text).get("jobs") or {})
    listed = [_job_url(base, job) for job in jobs.get("found") or []]
    if jobs.get("total") and not any(listed):
        raise RuntimeError("Het bord meldt vacatures, maar de pagina noemt er geen")
    known = store.source_urls_seen_longest_ago(source["id"], int(source.get("recheck", 60)))
    budget = limit_per_source or int(source.get("detail_cap", PAGE_CAP))
    for url in dict.fromkeys(url for url in [*listed, *known] if url):
        if _skip_recent_detail(url, entry, summary):
            continue
        if budget <= 0:
            break
        budget -= 1
        page = safe_detail_get(client, url, entry)
        time.sleep(float(source.get("delay", 1.0)))
        if page is None or page.status_code >= 400:
            continue
        try:
            job = (next_state(page.text).get("jobs") or {}).get("currentJob") or {}
        except ValueError:
            continue
        if not job.get("title") or job.get("status") != "active" or job.get("closedAt") or job.get("deactivatedAt"):
            continue  # closed: no longer seen, so it expires on its own
        place = _place(job.get("locations") or [])
        if not place and job.get("locations"):
            continue  # abroad
        text = strip_html(job.get("description") or "")
        description = text if len(text) >= SHORT_SUMMARY else best_description(text, page.text)
        stored = make_job(
            source["id"],
            url,
            str(job["title"]),
            company=(job.get("organization") or {}).get("name") or "",
            location=place,
            description=description,
            posted_at=str(job.get("postedAt") or ""),
        )
        _, created = store.upsert_job(stored)
        _tally(entry, summary, created)
    store.update_source_status(source["id"], "ok")


scan = _scan_getro


KINDS = ("getro-html",)
