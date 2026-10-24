"""jsonld source adapter."""
from __future__ import annotations

import json
import re
import time
from html import unescape

from .. import store
from .common import _skip_recent_detail, _tally, detail_get, make_job, page_text, strip_html


def jsonld_jobs(raw_html: str) -> list[dict]:
    """All schema.org JobPosting objects on a page (handles @graph and arrays)."""
    found: list[dict] = []
    for block in re.findall(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", raw_html, re.S | re.I
    ):
        try:
            node = json.loads(unescape(block))
        except ValueError:
            continue
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                types = current.get("@type")
                types = types if isinstance(types, list) else [types]
                if "JobPosting" in [t for t in types if isinstance(t, str)]:
                    found.append(current)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return found



def jsonld_company(data: dict) -> str:
    org = data.get("hiringOrganization") or {}
    if isinstance(org, dict):
        return str(org.get("name") or "")
    return str(org or "")



def jsonld_location(data: dict) -> tuple[str, float | None, float | None]:
    location = data.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else {}
    if not isinstance(location, dict):
        return "", None, None
    address = location.get("address") or {}
    if not isinstance(address, dict):
        address = {}
    label = ", ".join(
        str(part) for part in (address.get("addressLocality"), address.get("addressRegion")) if part
    )
    geo = location.get("geo") or {}
    lat = lon = None
    if isinstance(geo, dict):
        try:
            lat = float(geo.get("latitude"))
            lon = float(geo.get("longitude"))
        except (TypeError, ValueError):
            lat = lon = None
    return label, lat, lon



def _scan_sitemap_jsonld(client, source, entry, summary, limit_per_source=None) -> None:
    response = client.get(source["sitemap_url"])
    response.raise_for_status()
    urls = re.findall(r"<loc>([^<]+)</loc>", response.text)
    if "<sitemapindex" in response.text:
        child_pattern = source.get("child_pattern") or r".*"
        children = [url for url in urls if re.search(child_pattern, url)]
        urls = []
        for child in children[: int(source.get("child_limit", 4))]:
            child_response = client.get(child)
            if child_response.status_code < 400:
                urls.extend(re.findall(r"<loc>([^<]+)</loc>", child_response.text))
            time.sleep(0.4)
    pattern = source.get("url_pattern")
    selected = [url for url in urls if not pattern or re.search(pattern, url)]
    limit = limit_per_source or int(source.get("per_page", 40))
    seen: set[str] = set()
    for url in selected:
        if url in seen:
            continue
        seen.add(url)
        if len(seen) > limit:
            break
        if _skip_recent_detail(url, entry, summary):
            continue
        page = detail_get(client, url)
        if page.status_code >= 400:
            continue
        items = jsonld_jobs(page.text)
        if not items:
            continue
        data = items[0]
        title = str(data.get("title") or "").strip()
        if not title:
            continue
        location, lat, lon = jsonld_location(data)
        description = strip_html(data.get("description")) or page_text(page.text)
        job = make_job(
            source["id"],
            url,
            title,
            company=jsonld_company(data),
            location=location,
            description=description,
            posted_at=str(data.get("datePosted") or ""),
            lat=lat,
            lon=lon,
        )
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
        time.sleep(0.4)
    store.update_source_status(source["id"], "ok")



scan = _scan_sitemap_jsonld


KINDS = ('sitemap-jsonld',)
