"""rijksoverheid source adapter."""

from __future__ import annotations

import re
import time

import httpx
from bs4 import BeautifulSoup

from .. import store
from .common import _skip_recent_detail, _tally, detail_get, make_job


def _hn_ref(client, listing_url: str, fallback: str) -> str:
    """Discover the current render-tree ref for the vacancy results component."""
    try:
        shell = client.get(listing_url)
        refs = re.findall(r"_hn:ref=([A-Za-z0-9_]+)", shell.text)
        if refs:
            return refs[0]
    except httpx.HTTPError:
        pass
    return fallback


def _parse_hn_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    for section in soup.select("section.vacancy"):
        link = section.select_one('a[data-govan-action="view-card"]')
        if link is None:
            continue
        employer = section.select_one("p.vacancy__employer")
        labels: dict[str, str] = {}
        for item in section.select(".job-short-info__item"):
            icon = item.select_one(".job-short-info__icon")
            value = item.select_one(".job-short-info__value")
            label = (icon.get("title") if icon else "") or ""
            if label and value is not None:
                labels[label] = value.get_text(" ", strip=True)
        cards.append(
            {
                "title": link.get_text(" ", strip=True),
                "href": (link.get("href") or "").strip(),
                "employer": employer.get_text(" ", strip=True) if employer else "",
                "labels": labels,
            }
        )
    return cards


def _rijksoverheid_detail(client, url: str) -> tuple[str, str, str, str]:
    page = detail_get(client, url)
    if page.status_code >= 400:
        return "", "", "", ""
    soup = BeautifulSoup(page.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    meta: dict[str, str] = {}
    for element in soup.find_all("meta"):
        key = element.get("property") or element.get("name")
        if key in ("description", "og:title") and element.get("content"):
            meta[key] = element["content"]
    main = soup.find("main") or soup
    heading = main.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    text = re.sub(r"\s+", " ", main.get_text(" ", strip=True))
    marker = text.find("Dit ga je doen")
    if marker > 0:
        text = text[marker:]
    location = hours = ""
    match = re.search(
        r" in ([A-ZÀ-Ý][\w' .-]{2,30}?) voor ([\d,.]+) uur", meta.get("description", "")
    )
    if match:
        location = match.group(1).strip()
        hours = f"{match.group(2)} uur"
    return title, location, hours, text[:20000]


def _scan_rijksoverheid(client, source, entry, summary, limit_per_source=None) -> None:
    base = source["base_url"].rstrip("/")
    listing = base + source.get("path", "/vacatures")
    ref = source.get("hn_ref") or _hn_ref(client, listing, "r18_r1_r4")
    pages = int(source.get("pages", 2))
    per_page = int(source.get("per_page", 20))
    limit = limit_per_source or pages * per_page
    cards: list[dict] = []
    for page_number in range(1, pages + 1):
        suffix = f"&pagina={page_number}" if page_number > 1 else ""
        url = f"{listing}?_hn:type=resource&_hn:ref={ref}&_hn:rid=vacancy-results{suffix}"
        response = client.get(url)
        if response.status_code >= 400:
            ref = _hn_ref(client, listing, ref)
            url = f"{listing}?_hn:type=resource&_hn:ref={ref}&_hn:rid=vacancy-results{suffix}"
            response = client.get(url)
        if response.status_code >= 400:
            break
        cards.extend(_parse_hn_cards(response.text))
        time.sleep(0.4)
    if not cards:
        raise ValueError("geen vacaturekaarten gevonden; controleer de _hn:ref")
    for card in cards[:limit]:
        href = card["href"]
        if not href:
            continue
        job_url = href if href.startswith("http") else base + href
        if _skip_recent_detail(job_url, entry, summary):
            continue
        title, meta_location, meta_hours, description = _rijksoverheid_detail(client, job_url)
        title = title or card["title"]
        labels = card.get("labels") or {}
        location = labels.get("Locatie") or meta_location
        hours = labels.get("Aantal uren") or labels.get("Uren") or meta_hours
        facts = " · ".join(f"{value}" for value in labels.values() if value)
        header = "\n".join(
            part
            for part in (
                "Rijksoverheid (Werken bij de Overheid)",
                f"Organisatie: {card['employer']}" if card["employer"] else "",
                f"Kenmerken: {facts}" if facts else "",
                f"Locatie: {location}" if location else "",
                f"Uren: {hours}" if hours else "",
            )
            if part
        )
        job = make_job(
            source["id"],
            job_url,
            title,
            company=card["employer"] or "Rijksoverheid",
            location=location,
            hours=hours,
            description=f"{header}\n\n{description}".strip(),
            posted_at="",
        )
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
        time.sleep(0.35)
    store.update_source_status(source["id"], "ok")


scan = _scan_rijksoverheid


KINDS = ("hn-resource",)
