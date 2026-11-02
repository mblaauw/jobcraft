"""Add one vacancy yourself: from a pasted link or pasted text. Private to the account.

The fetch runs on behalf of a logged-in user, so every hop is checked against private
network addresses, and a site's robots rules are respected: closed sites use pasted text.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from . import store, tenancy
from .adapters.common import make_job, page_text, strip_html
from .adapters.jsonld import jsonld_company, jsonld_jobs, jsonld_location
from .source_http import USER_AGENT

MAX_BYTES = 3 * 1024 * 1024
MAX_HOPS = 4
CLOSED = "Deze site laat zich niet automatisch lezen. Plak de vacaturetekst hieronder."


class ImportBlocked(ValueError):
    """The page cannot be read automatically; pasting the text still works."""


def _web_address(url: str):
    try:
        parts = urlsplit(url)
        # Reading .port raises on a port that cannot exist.
        valid = (
            parts.scheme in ("http", "https")
            and parts.hostname
            and not parts.username
            and (parts.port is None or parts.port > 0)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ImportBlocked("Dit is geen geldig webadres. Gebruik een link die begint met https://.")
    return parts


def _check_public(url: str) -> None:
    parts = _web_address(url)
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        raise ImportBlocked(CLOSED) from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ImportBlocked(CLOSED)


def _get(client: httpx.Client, url: str) -> httpx.Response:
    for _ in range(MAX_HOPS):
        _check_public(url)
        with client.stream("GET", url) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get("location", ""))
                continue
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ImportBlocked(CLOSED)
            # The body is already decompressed; keeping this header would decode it twice.
            headers = httpx.Headers(response.headers)
            headers.pop("content-encoding", None)
            return httpx.Response(
                response.status_code, headers=headers, content=body, request=response.request
            )
    raise ImportBlocked(CLOSED)


def fetch_page(url: str) -> dict:
    """Vacancy fields from a public page; raises ImportBlocked when it cannot be read."""
    parts = _web_address(url)
    with httpx.Client(
        timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    ) as client:
        try:
            robots = _get(client, f"{parts.scheme}://{parts.netloc}/robots.txt")
            if robots.status_code < 400:
                policy = RobotFileParser()
                policy.parse(robots.text.splitlines())
                if not policy.can_fetch(USER_AGENT, url):
                    raise ImportBlocked(CLOSED)
            elif robots.status_code in (401, 403):
                raise ImportBlocked(CLOSED)
            page = _get(client, url)
        except httpx.HTTPError:
            raise ImportBlocked(CLOSED) from None
    if page.status_code >= 400:
        raise ImportBlocked(CLOSED)
    items = jsonld_jobs(page.text)
    if items:
        data = items[0]
        location, lat, lon = jsonld_location(data)
        return {
            "title": str(data.get("title") or "").strip(),
            "company": jsonld_company(data),
            "location": location,
            "description": strip_html(data.get("description")) or page_text(page.text),
            "posted_at": str(data.get("datePosted") or ""),
            "lat": lat,
            "lon": lon,
        }
    soup = BeautifulSoup(page.text, "html.parser")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    description = page_text(page.text)
    if not title or len(description) < 200:  # A login wall or an empty JavaScript shell.
        raise ImportBlocked(CLOSED)
    return {"title": title, "company": "", "location": "", "description": description}


def _private_url(url: str, description: str) -> str:
    """Own URL per account: the shared table keeps one row per URL."""
    owner = hashlib.sha256(tenancy.user_id().encode()).hexdigest()[:12]
    if url:
        return f"{url.split('#')[0]}#jobcraft-{owner}"
    return f"https://import.jobcraft.invalid/{owner}/{hashlib.sha256(description.encode()).hexdigest()[:20]}"


def add(fields: dict, url: str = "") -> int:
    """Store the vacancy for this account only and mark it as saved."""
    if url:  # With pasted text the link was never fetched, so it is unchecked until here.
        try:
            _web_address(url)
        except ImportBlocked:
            url = ""
    job = make_job(
        store.import_source(),
        _private_url(url, fields.get("description") or ""),
        fields.get("title") or "Vacature zonder titel",
        company=fields.get("company") or "",
        location=fields.get("location") or "",
        description=fields.get("description") or "",
        posted_at=fields.get("posted_at") or "",
        lat=fields.get("lat"),
        lon=fields.get("lon"),
    )
    job_id, _created = store.upsert_job(job)
    store.update_job_status(job_id, "saved")
    return job_id
