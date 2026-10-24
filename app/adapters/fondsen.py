"""fondsen source adapter."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from .. import store
from .common import _tally, make_job, page_text


def _scan_fondsen(client, source, entry, summary, limit_per_source=None) -> None:
    """HTML adapter for fondsen.org (its WP REST route 404s since 2026-10-17)."""
    listing_url = source["base_url"].rstrip("/") + source["path"]
    response = client.get(listing_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    links: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        if re.fullmatch(r"/vacature/[A-Za-z0-9_-]+/?", href):
            absolute = source["base_url"].rstrip("/") + href.rstrip("/")
            if absolute not in links:
                links.append(absolute)
    limit = limit_per_source or int(source.get("per_page", 30))
    for link in links[:limit]:
        page = client.get(link)
        if page.status_code >= 400:
            continue
        page_soup = BeautifulSoup(page.text, "html.parser")
        heading = page_soup.find("h1")
        title = heading.get_text(" ", strip=True) if heading else ""
        description = page_text(page.text)
        if not title:
            title = description[:80]
        _, created = store.upsert_job(make_job(source["id"], link, title, description=description))
        _tally(entry, summary, created)
        time.sleep(0.4)
    store.update_source_status(source["id"], "ok")



scan = _scan_fondsen


KINDS = ('html-fondsen',)
