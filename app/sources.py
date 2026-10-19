"""Job-source adapters and the scan runner.

Adapters (see `seeds/sources.yaml` for the verified configuration):

- ``wp-posts`` / ``wp-job-listings``  WordPress REST (title, date, link, content)
- ``html-fondsen``                    fondsen.org HTML list + detail pages
- ``html-listing``                    generic list -> detail pages, configurable
                                      with `link_pattern`, `pages`, optional
                                      `location_from_slug`
- ``sitemap-jsonld``                  sitemap -> detail pages with schema.org
                                      JobPosting JSON-LD (pagination-free)
- ``rss``                             RSS/Atom feed
- ``exenzo-json``                     Exenzo jobboard API (Greenjobs)

Every adapter normalises to the same job dict and calls ``store.upsert_job``.
Polite scanning: sequential requests with a small delay per source.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from html import unescape
from xml.etree import ElementTree

import httpx
import yaml
from bs4 import BeautifulSoup

from . import config, store
from .util import now_iso

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SEED_FILE = config.BASE_DIR / "seeds" / "sources.yaml"
DATA_FILE = config.DATA_DIR / "sources.yaml"


def ensure_sources_file() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists() and SEED_FILE.exists():
        shutil.copy2(SEED_FILE, DATA_FILE)


def load_sources() -> list[dict]:
    ensure_sources_file()
    if not DATA_FILE.exists():
        return []
    payload = yaml.safe_load(DATA_FILE.read_text()) or {}
    sources = payload.get("sources") or []
    return [source for source in sources if isinstance(source, dict) and source.get("id")]


def set_enabled(source_id: str, enabled: bool) -> bool:
    """Flip one source's `enabled` flag in data/sources.yaml, keeping the file's comments."""
    ensure_sources_file()
    text = DATA_FILE.read_text()
    block = re.compile(
        rf"(^[ \t]*-[ \t]*id:[ \t]*{re.escape(source_id)}[ \t]*\n)(.*?)(?=^[ \t]*-[ \t]*id:|\Z)",
        re.M | re.S,
    )
    match = block.search(text)
    if not match:
        return False
    body = match.group(2)
    desired = "true" if enabled else "false"
    if re.search(r"^[ \t]*enabled:[ \t]*(true|false)[ \t]*$", body, re.M):
        new_body = re.sub(
            r"^([ \t]*enabled:[ \t]*)(true|false)([ \t]*)$",
            lambda m: f"{m.group(1)}{desired}{m.group(3)}",
            body,
            count=1,
            flags=re.M,
        )
    else:
        new_body = body.rstrip("\n") + f"\n    enabled: {desired}\n"
    DATA_FILE.write_text(text[: match.start(2)] + new_body + text[match.end(2) :])
    return True


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
        "posted_at": posted_at or "",
        "lat": lat,
        "lon": lon,
        "content_hash": hashlib.sha256(f"{title}:{description}".encode()).hexdigest(),
    }


def _tally(entry: dict, summary: dict, created: bool) -> None:
    entry["items"] += 1
    entry["new" if created else "updated"] += 1
    summary["new" if created else "updated"] += 1


# ---------------------------------------------------------------- WordPress


def normalize_wp_item(source: dict, item: dict, client: httpx.Client | None = None) -> dict | None:
    title = strip_html(item.get("title"))
    url = item.get("link")
    if not title or not isinstance(url, str) or not url.startswith("http"):
        return None
    description = strip_html(item.get("content"))
    if not description and source.get("content_from_page") and client is not None:
        try:
            response = client.get(url, headers={"User-Agent": USER_AGENT})
            if response.status_code < 400:
                description = page_text(response.text)
            time.sleep(0.4)
        except httpx.HTTPError:
            description = ""
    company, clean_title = split_company(title)
    return make_job(
        source["id"],
        url,
        clean_title,
        company=company,
        description=description,
        posted_at=str(item.get("date") or ""),
    )


# ---------------------------------------------------------------- fondsen.org


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


# ---------------------------------------------------------------- JSON-LD


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
        page = client.get(url)
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


# ---------------------------------------------------------------- html-listing


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


# ---------------------------------------------------------------- RSS / Atom


def _scan_rss(client, source, entry, summary, limit_per_source=None) -> None:
    feed_url = source.get("feed_url") or (
        source["base_url"].rstrip("/") + source.get("path", "/feed/")
    )
    response = client.get(feed_url)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    items = [element for element in root.iter() if element.tag.split("}")[-1] in ("item", "entry")]
    limit = limit_per_source or int(source.get("per_page", 30))
    for item in items[:limit]:

        def child_text(names) -> str:
            for child in item:
                if child.tag.split("}")[-1] in names:
                    return (child.text or "").strip()
            return ""

        title = child_text(("title",))
        link_element = next((child for child in item if child.tag.split("}")[-1] == "link"), None)
        link = ""
        if link_element is not None:
            link = (link_element.text or "").strip() or link_element.get("href", "")
        description = strip_html(child_text(("description", "summary", "content")))
        posted_at = child_text(("pubDate", "published", "updated"))
        if not title or not link:
            continue
        _, created = store.upsert_job(
            make_job(
                source["id"],
                link,
                title,
                description=description,
                posted_at=posted_at,
            )
        )
        _tally(entry, summary, created)
    store.update_source_status(source["id"], "ok")


# ---------------------------------------------------------------- Exenzo API


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


def _scan_exenzo(client, source, entry, summary, limit_per_source=None) -> None:
    base = source["base_url"].rstrip("/")
    path = source.get("path", "/api/public/v1/jobs")
    per_page = int(source.get("per_page", 100))
    max_pages = int(source.get("max_pages", 3))
    limit = limit_per_source or per_page * max_pages
    collected = 0
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
            _, created = store.upsert_job(job)
            _tally(entry, summary, created)
            collected += 1
            if collected >= limit:
                break
        if collected >= limit or len(items) < per_page:
            break
        time.sleep(0.5)
    store.update_source_status(source["id"], "ok")


# ---------------------------------------------------------------- Defensie


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
        if enrich:
            path = re.sub(r"^https?://[^/]+", "", job["url"]) or "/"
            payloads = _defensie_component_payloads(client, path)
            details, location = _defensie_details(payloads)
            if details:
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


# ---------------------------------------------------------------- Rijksoverheid


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
    page = client.get(url)
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


# ---------------------------------------------------------------- local-json import


def _scan_local_json(client, source, entry, summary, limit_per_source=None) -> None:
    """Read pre-fetched vacancies (browser import or manual) from data/imports/*.json."""
    relative = source.get("file") or f"imports/{source['id']}.json"
    path = config.DATA_DIR / relative
    if not path.exists():
        raise FileNotFoundError(
            f"{path} ontbreekt; draai eerst scripts/browser_import.py (of vul het bestand met de hand)"
        )
    payload = json.loads(path.read_text())
    items = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError('importbestand moet een lijst of {"jobs": [...]} zijn')
    limit = limit_per_source or int(source.get("per_page", 100))
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("link")
        title = item.get("title")
        if not url or not title:
            continue
        job = make_job(
            source["id"],
            url,
            str(title),
            company=item.get("company") or item.get("employer") or "",
            location=item.get("location") or "",
            hours=item.get("hours") or "",
            description=item.get("description") or "",
            posted_at=item.get("posted_at") or "",
            lat=item.get("lat"),
            lon=item.get("lon"),
        )
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
    store.update_source_status(source["id"], "ok")


# ---------------------------------------------------------------- runner


def scan(limit_per_source: int | None = None) -> dict:
    sources = [source for source in load_sources() if source.get("enabled", True)]
    summary: dict = {
        "started_at": now_iso(),
        "finished_at": None,
        "sources": {},
        "new": 0,
        "updated": 0,
        "errors": [],
    }
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30
    ) as client:
        for source in sources:
            kind = source.get("kind")
            entry: dict = {"status": "ok", "new": 0, "updated": 0, "items": 0}
            try:
                if kind in ("wp-posts", "wp-job-listings"):
                    url = source["base_url"].rstrip("/") + source["path"]
                    params = {"per_page": int(source.get("per_page", 30))}
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    items = response.json()
                    if not isinstance(items, list):
                        raise ValueError("unexpected payload (not a list)")
                    limit = limit_per_source or len(items)
                    for item in items[:limit]:
                        if not isinstance(item, dict):
                            continue
                        job = normalize_wp_item(source, item, client)
                        if not job:
                            continue
                        _, created = store.upsert_job(job)
                        _tally(entry, summary, created)
                    store.update_source_status(source["id"], "ok")
                elif kind == "html-fondsen":
                    _scan_fondsen(client, source, entry, summary, limit_per_source)
                elif kind == "html-listing":
                    _scan_html_listing(client, source, entry, summary, limit_per_source)
                elif kind == "sitemap-jsonld":
                    _scan_sitemap_jsonld(client, source, entry, summary, limit_per_source)
                elif kind == "rss":
                    _scan_rss(client, source, entry, summary, limit_per_source)
                elif kind == "exenzo-json":
                    _scan_exenzo(client, source, entry, summary, limit_per_source)
                elif kind == "defensie-cms":
                    _scan_defensie(client, source, entry, summary, limit_per_source)
                elif kind == "hn-resource":
                    _scan_rijksoverheid(client, source, entry, summary, limit_per_source)
                elif kind == "local-json":
                    _scan_local_json(client, source, entry, summary, limit_per_source)
                else:
                    entry["status"] = "skipped"
                    entry["reason"] = f"adapter '{kind}' not implemented yet"
                    summary["sources"][source["id"]] = entry
                    continue
            except Exception as exc:  # noqa: BLE001 - surface per-source failures
                entry["status"] = "error"
                entry["reason"] = f"{type(exc).__name__}: {exc}"[:300]
                summary["errors"].append({"source": source["id"], "reason": entry["reason"]})
                store.update_source_status(source["id"], "error")
            summary["sources"][source["id"]] = entry
            time.sleep(0.5)
    summary["finished_at"] = now_iso()
    return summary
