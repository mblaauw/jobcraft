"""Find what a public website says about an employer. Only the employer's name goes out.

The gateway's models cannot browse, so the app reads the pages itself and hands the text
over, with the address, so each fact in a letter can point back to where it stands. Nothing
is found for many small employers, and that is fine: then the vacancy text has to do.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .adapters.common import page_text
from .job_import import ImportBlocked, _get
from .source_http import USER_AGENT

log = logging.getLogger("jobcraft")

MAX_PAGES = 3
PAGE_CHARS = 5000
_ABOUT = re.compile(r"\bover[- ]?(ons|de[- ](gemeente|organisatie|provincie))\b|\babout\b|organisatie|wie[- ]zijn|missie|strategie|ambitie|visie", re.I)
_LEGAL = re.compile(r"\b(b\.?v\.?|n\.?v\.?|holding|group|groep|nederland|the netherlands|stichting|vof)\b", re.I)
_GOVERNMENT = re.compile(r"^(gemeente|provincie|waterschap|hoogheemraadschap)\s+", re.I)


def _slug(company: str) -> str:
    name = _LEGAL.sub(" ", _GOVERNMENT.sub("", company))
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _name_words(company: str) -> list[str]:
    name = _LEGAL.sub(" ", company)
    return [word.lower() for word in re.findall(r"[^\W\d_]{3,}", name)]


def _mentions(text: str, company: str) -> bool:
    words = _name_words(company)
    lowered = text.lower()
    return bool(words) and all(word in lowered for word in words)


def _fetch(client: httpx.Client, url: str, robots: dict) -> str:
    """HTML of a public page, or '' when robots rules, a private address or an error stop it."""
    parts = urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc}"
    try:
        if host not in robots:
            answer = _get(client, f"{host}/robots.txt")
            policy = RobotFileParser()
            policy.parse(answer.text.splitlines() if answer.status_code < 400 else [])
            robots[host] = policy
        if not robots[host].can_fetch(USER_AGENT, url):
            return ""
        page = _get(client, url)
    except (ImportBlocked, httpx.HTTPError, UnicodeError, ValueError):
        return ""
    if page.status_code >= 400 or "html" not in page.headers.get("content-type", "html"):
        return ""
    return page.text


def _site_of(job: dict, company: str) -> list[str]:
    """Addresses that could be the employer's own site; each is verified by its content."""
    slug = _slug(company)
    candidates = []
    url = str(job.get("url") or "").split("#")[0]
    host = urlsplit(url).hostname or ""
    if slug and slug in host.replace("-", "").replace(".", ""):
        candidates.append(f"https://{host}/")
    if slug:
        candidates += [f"https://www.{slug}.nl/", f"https://www.{slug}.com/", f"https://{slug}.nl/"]
    return list(dict.fromkeys(candidates))


def _about_links(html: str, base: str) -> list[str]:
    host = urlsplit(base).hostname
    links = []
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        target = urljoin(base, anchor["href"]).split("#")[0]
        if urlsplit(target).hostname != host or target.rstrip("/") == base.rstrip("/"):
            continue
        label = f"{anchor.get_text(' ', strip=True)} {urlsplit(target).path}"
        if _ABOUT.search(label) and not re.search(r"vacature|privacy|cookie|login|\.pdf$", label, re.I):
            links.append(target)
    return list(dict.fromkeys(links))


def _wikipedia(client: httpx.Client, company: str) -> dict | None:
    title = _LEGAL.sub(" ", company).strip()
    if not title:
        return None
    try:
        answer = client.get(
            f"https://nl.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}",
            follow_redirects=True,
        )
        data = answer.json() if answer.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    extract = str(data.get("extract") or "")
    if data.get("type") != "standard" or not _mentions(extract, title):
        return None
    address = (data.get("content_urls") or {}).get("desktop", {}).get("page", "")
    return {"url": address, "title": "Wikipedia", "text": extract[:PAGE_CHARS]}


def gather(job: dict) -> list[dict]:
    """Up to a few pages about the employer: [{url, title, text}]. Never raises."""
    company = str(job.get("company") or "").strip()
    if len(company) < 3:
        return []
    pages: list[dict] = []
    robots: dict[str, RobotFileParser] = {}
    try:
        with httpx.Client(
            timeout=10, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        ) as client:
            for address in _site_of(job, company):
                html = _fetch(client, address, robots)
                text = page_text(html) if html else ""
                if len(text) < 200 or not _mentions(text, company.split()[-1]):
                    continue  # A parked domain or someone else with a similar name.
                pages.append({"url": address, "title": "Website", "text": text[:PAGE_CHARS]})
                for link in _about_links(html, address)[:4]:
                    if len(pages) >= MAX_PAGES:
                        break
                    more = page_text(_fetch(client, link, robots) or "")
                    if len(more) >= 200:
                        pages.append({"url": link, "title": "Website", "text": more[:PAGE_CHARS]})
                break
            encyclopedia = _wikipedia(client, company)
            if encyclopedia and len(pages) <= MAX_PAGES:
                pages.append(encyclopedia)
    except Exception as exc:  # Research is a bonus: a letter must never fail because of it.
        log.warning("employer research failed: %s", type(exc).__name__)
    return pages
