"""html listing source adapter."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from .. import store
from .common import _tally, make_job, page_text, strip_html
from .jsonld import jsonld_company, jsonld_jobs, jsonld_location


def _scan_html_listing(client, source, entry, summary, limit_per_source=None) -> None:
    listing = source.get("listing_url") or (source["base_url"].rstrip("/") + source.get("path", ""))
    base = source["base_url"].rstrip("/")
    link_pattern = source["link_pattern"]
    pages = int(source.get("pages", 1))
    links: list[str] = []
    for page_number in range(1, pages + 1):
        url = listing.replace("{page}", str(page_number))
        response = client.get(url)
        if response.status_code >= 400:
            if page_number == 1:
                response.raise_for_status()
            break
        for href in re.findall(r'href="([^"]+)"', response.text):
            if not re.search(link_pattern, href):
                continue
            absolute = (
                href
                if href.startswith("http")
                else base + ("" if href.startswith("/") else "/") + href
            )
            if absolute not in links:
                links.append(absolute)
        time.sleep(0.4)
    limit = limit_per_source or int(source.get("per_page", 30))
    slug_location = source.get("location_from_slug")
    for link in links[:limit]:
        page = client.get(link)
        if page.status_code >= 400:
            continue
        items = jsonld_jobs(page.text)
        title = company = location = ""
        lat = lon = None
        if items:
            data = items[0]
            title = str(data.get("title") or "").strip()
            company = jsonld_company(data)
            location, lat, lon = jsonld_location(data)
            description = strip_html(data.get("description")) or page_text(page.text)
        else:
            soup = BeautifulSoup(page.text, "html.parser")
            heading = soup.find("h1")
            title = heading.get_text(" ", strip=True) if heading else ""
            description = page_text(page.text)
        if slug_location and not location:
            match = re.search(slug_location, link)
            if match:
                location = match.group(1).replace("-", " ").strip().title()
        if not location and source.get("default_location"):
            location = source["default_location"]
        if not company and source.get("company_from_host"):
            host = re.sub(r"^https?://", "", link).split("/")[0]
            host = re.sub(r"^(www|werkenbij)\.", "", host)
            label = host.split(".")[0].replace("-", " ").strip()
            if label and label not in {"vacatures", "www"}:
                company = label.title()
        if not company and source.get("default_company"):
            company = source["default_company"]
        if not title:
            continue
        job = make_job(
            source["id"], link, title, company=company, location=location, description=description
        )
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
        time.sleep(0.4)
    store.update_source_status(source["id"], "ok")



scan = _scan_html_listing


KINDS = ('html-listing',)
