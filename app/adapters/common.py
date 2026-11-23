"""common source adapter."""

from __future__ import annotations

import hashlib
import re

import httpx
from bs4 import BeautifulSoup

from .. import geo, store


def detail_get(client, url: str, **kwargs):
    response = client.get(url, **kwargs)
    if response.status_code == 200:
        store.record_detail_fetch(url)
    return response


DETAIL_FAILURE_LIMIT = 5


def safe_detail_get(client, url: str, entry: dict):
    """One unreachable or robots-disallowed page skips that vacancy, not the whole source.

    Repeated failures still fail the source: a partial scan must not mark every
    unseen vacancy as expired. Only failures that say something about the source
    count: a page that redirects to itself is that page's defect, and on a re-scan,
    where the pages fetched recently are skipped, a handful of such pages would
    otherwise be the only fetches and fail a healthy source (Unique, 2026-11-07).
    """
    try:
        response = detail_get(client, url)
    except httpx.HTTPError as exc:
        entry["details_skipped"] = entry.get("details_skipped", 0) + 1
        # A site that asks not to be crawled is an answer, not a failing source;
        # a redirect loop is one broken page.
        if str(exc).startswith("Robots policy disallows") or isinstance(exc, httpx.TooManyRedirects):
            return None
        entry["failure_streak"] = entry.get("failure_streak", 0) + 1
        if entry["failure_streak"] >= DETAIL_FAILURE_LIMIT:
            raise
        return None
    if response.status_code == 429 or response.status_code >= 500:
        # The site is rate-limiting or down. Each such answer already cost the client's own
        # retries and pauses; going on would hold the one scan slot for hours and bring nothing.
        entry["details_skipped"] = entry.get("details_skipped", 0) + 1
        entry["failure_streak"] = entry.get("failure_streak", 0) + 1
        if entry["failure_streak"] >= DETAIL_FAILURE_LIMIT:
            raise httpx.HTTPStatusError(
                f"{DETAIL_FAILURE_LIMIT} pagina's achter elkaar gaven HTTP {response.status_code}",
                request=getattr(response, "request", None),
                response=response,
            )
        return response
    entry["failure_streak"] = 0
    return response


def _skip_recent_detail(url: str, entry: dict, summary: dict) -> bool:
    if not url:
        return False
    if store.touch_recent_detail(url):
        _tally(entry, summary, False)
        return True
    # Read lately and left out (closed since): not read again yet, and not marked as seen.
    return store.read_and_left_out(url)


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


SHORT_SUMMARY = 400


def best_description(summary: str, raw_html: str) -> str:
    """Structured data is sometimes only a one-line summary; the page then has the real text."""
    if len(summary) >= SHORT_SUMMARY:
        return summary
    full = page_text(raw_html)
    return full if len(full) > len(summary) else summary


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
        # Many boards leave the place out of their fields but state it in the text.
        "location": (location or "").strip() or geo.place_in_text(description),
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
