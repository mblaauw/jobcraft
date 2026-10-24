"""defensie source adapter."""
from __future__ import annotations

import hashlib
import json
import re
import time
from xml.etree import ElementTree

from .. import store
from .common import _skip_recent_detail, _tally, make_job, strip_html

DEFENSIE_HOST = "https://werkenbijdefensie.nl"


def _defensie_component_payloads(client, href: str) -> list[dict]:
    """Fetch the CMS components of one vacancy page (public JSON API)."""
    route = client.get(f"{DEFENSIE_HOST}/api/v1/routing/route", params={"href": href})
    if route.status_code >= 400:
        return []
    components = ((route.json() or {}).get("data") or {}).get("components") or []
    payloads: list[dict] = []
    for component in components[:8]:
        raw = component.get("id")
        if not raw:
            continue
        response = client.get(
            f"{DEFENSIE_HOST}/api/v1/component/component", params={"component_id": raw}
        )
        if response.status_code >= 400:
            continue
        try:
            payloads.append(response.json())
        except ValueError:
            continue
        time.sleep(0.3)
    return payloads



def _defensie_details(payloads: list[dict]) -> tuple[str, str]:
    """Return (description, location) from the component payloads."""
    bodies: list[str] = []
    location = ""
    for payload in payloads:
        blob = json.dumps(payload, ensure_ascii=False)
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            for key in ("body", "snippet_description", "description", "intro"):
                value = data.get(key)
                if isinstance(value, str) and len(value) > 40:
                    bodies.append(strip_html(value))
        if not location:
            match = re.search(
                r'"(?:location|city|place|work_location|standplaats)"\s*:\s*"([^"]{2,40})"', blob
            )
            if match:
                location = match.group(1)
    unique = list(dict.fromkeys(body for body in bodies if body))
    joined = "\n\n".join(unique)
    if not location and joined:
        # The vacancy body lists "Standplaats <plaats>" among other labelled facts.
        labels = (
            "Standplaats|Contractduur|Uren per week|Salaris|Opleidingsniveau|Functiegroep|"
            "Rijbewijs|Vacaturenummer|Reizen|Werktijden|Aantal uur"
        )
        match = re.search(rf"Standplaats\s+(.*?)(?=\s+(?:{labels})\b|$)", joined)
        if match:
            location = re.sub(r"\s+", " ", match.group(1)).strip()[:60]
    return joined[:20000], location



def normalize_defensie(source: dict, item: ElementTree.Element) -> dict | None:
    def text(tag: str) -> str:
        element = item.find(tag)
        if element is None:
            return ""
        return (element.text or "").strip()

    title = strip_html(text("title"))
    url = text("url")
    if not title or not url.startswith("http"):
        return None
    unit = text("business_unit")
    work_areas = [
        strip_html(element.text or "") for element in item.findall("work_areas/work_area/title")
    ]
    additionals = item.find("additionals")
    salary = days_off = licence = ""
    if additionals is not None:
        salary = strip_html((additionals.findtext("salary") or "").strip())
        days_off = strip_html((additionals.findtext("days_off") or "").strip())
        licence = strip_html((additionals.findtext("driver_license_provided") or "").strip())
    header = [
        "Defensie burgerfunctie (niet-militair)",
        f"Organisatieonderdeel: {unit}" if unit else "",
        f"Werkterrein: {', '.join(area for area in work_areas if area)}" if work_areas else "",
        f"Salaris: {salary}" if salary else "",
        f"Vakantiedagen: {days_off}" if days_off else "",
        f"Rijbewijs via Defensie: {licence}" if licence else "",
        f"Korte intro: {strip_html(text('short_intro'))}" if text("short_intro") else "",
    ]
    return make_job(
        source["id"],
        url,
        title,
        company=f"Defensie · {unit}" if unit else "Defensie",
        description="\n".join(part for part in header if part),
        posted_at="",
    )



def _scan_defensie(client, source, entry, summary, limit_per_source=None) -> None:
    feed_url = source["feed_url"]
    response = client.get(feed_url)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    items = [element for element in root.iter() if element.tag == "item"]
    limit = limit_per_source or int(source.get("per_page", 20))
    enrich = bool(source.get("enrich_details", True))
    for item in items[:limit]:
        job = normalize_defensie(source, item)
        if not job:
            continue
        if enrich and _skip_recent_detail(job["url"], entry, summary):
            continue
        if enrich:
            path = re.sub(r"^https?://[^/]+", "", job["url"]) or "/"
            payloads = _defensie_component_payloads(client, path)
            details, location = _defensie_details(payloads)
            if details:
                store.record_detail_fetch(job["url"])
                job["description"] = f"{job['description']}\n\n{details}"[:20000]
            if location:
                job["location"] = location
            job["content_hash"] = hashlib.sha256(
                f"{job['title']}:{job['description']}".encode()
            ).hexdigest()
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
        time.sleep(0.3)
    store.update_source_status(source["id"], "ok")



scan = _scan_defensie


KINDS = ('defensie-cms',)
