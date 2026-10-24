"""common source adapter."""

from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup

from .. import store

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def strip_html(value) -> str:
    if isinstance(value, dict):
        value = value.get("rendered", "")
    if not isinstance(value, str):
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def page_text(raw_html: str, limit: int = 20000) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:limit].strip()


def split_company(title: str) -> tuple[str, str]:
    """Culturele Vacatures often prefixes 'Organisatie: Functie'."""
    if ": " in title:
        prefix, rest = title.split(": ", 1)
        if 0 < len(prefix) <= 48 and rest.strip():
            return prefix.strip(), rest.strip()
    return "", title.strip()


def make_job(
    source_id: str,
    url: str,
    title: str,
    company: str = "",
    location: str = "",
    hours: str = "",
    description: str = "",
    posted_at: str = "",
    lat=None,
    lon=None,
) -> dict:
    return {
        "source_id": source_id,
        "url": url,
        "title": title.strip(),
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "hours": hours,
        "description": description or "",
        "posted_at": store.normalize_posted_at(posted_at),
        "lat": lat,
        "lon": lon,
        "content_hash": hashlib.sha256(f"{title}:{description}".encode()).hexdigest(),
    }


def _tally(entry: dict, summary: dict, created: bool) -> None:
    entry["items"] += 1
    entry["new" if created else "updated"] += 1
    summary["new" if created else "updated"] += 1
