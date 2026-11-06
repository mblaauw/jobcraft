"""jsonld source adapter."""
from __future__ import annotations

import json
import re
import time
from html import unescape

from bs4 import BeautifulSoup

from .. import store
from .common import (
    _skip_recent_detail,
    _tally,
    best_description,
    make_job,
    page_text,
    safe_detail_get,
    strip_html,
)


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
    if isinstance(address, str):  # schema.org allows plain text: "address": "Wognum"
        address = {"addressLocality": address.strip()}
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



def html_posting(raw_html: str, source: dict) -> dict | None:
    """A page without JobPosting: h1 as title, the page text, and place or employer from the
    <title> when the source's `title_pattern` has named groups `place` and `company`."""
    soup = BeautifulSoup(raw_html, "html.parser")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    if not title:
        return None
    head = soup.title.get_text(" ", strip=True) if soup.title else ""
    match = re.search(source["title_pattern"], head) if source.get("title_pattern") else None
    found = match.groupdict() if match else {}
    return {
        "title": title,
        "company": (found.get("company") or "").strip() or source.get("default_company", ""),
        "location": (found.get("place") or "").strip(),
        "description": page_text(raw_html),
        "posted_at": "",
    }


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
        page = safe_detail_get(client, url, entry)
        if page is None or page.status_code >= 400:
            continue
        items = jsonld_jobs(page.text)
        if items:
            data = items[0]
            location, lat, lon = jsonld_location(data)
            posting = {
                "title": str(data.get("title") or "").strip(),
                "company": jsonld_company(data),
                "location": location,
                "description": best_description(strip_html(data.get("description")), page.text),
                "posted_at": str(data.get("datePosted") or ""),
            }
        elif source.get("html_fallback"):  # boards that render vacancies without structured data
            posting, lat, lon = html_posting(page.text, source), None, None
        else:
            continue
        if not posting or not posting["title"]:
            continue
        job = make_job(
            source["id"],
            url,
            posting["title"],
            company=posting["company"],
            location=posting["location"] or source.get("default_location", ""),
            description=posting["description"],
            posted_at=posting["posted_at"],
            lat=lat,
            lon=lon,
        )
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
        time.sleep(0.4)
    store.update_source_status(source["id"], "ok")



scan = _scan_sitemap_jsonld


KINDS = ('sitemap-jsonld',)
