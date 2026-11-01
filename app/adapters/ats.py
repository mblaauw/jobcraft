"""Employer registry: public Greenhouse and Recruitee boards, one request per employer.

Adding an employer is one line in `seeds/employers.yaml`. Each board returns full
vacancy text in a single public response, so no detail pages are fetched.
"""

from __future__ import annotations

import re
import time
from html import unescape
from pathlib import Path

import httpx
import yaml

from .. import geo, store
from .common import _tally, make_job, strip_html

EMPLOYERS_FILE = Path(__file__).resolve().parents[2] / "seeds" / "employers.yaml"
_NETHERLANDS = re.compile(r"\b(?:netherlands|nederland|the hague)\b", re.I)
_ELSEWHERE = re.compile(
    r"\b(?:germany|deutschland|belgium|france|spain|italy|ireland|poland|portugal|sweden|denmark|"
    r"austria|switzerland|united kingdom|usa|united states|canada|india|singapore|australia)\b",
    re.I,
)


def in_netherlands(location: str | None) -> bool:
    text = location or ""
    if _NETHERLANDS.search(text):
        return True
    return not _ELSEWHERE.search(text) and geo._find_place(text) in geo.CORE_PLACES


def load_employers(path: Path = EMPLOYERS_FILE) -> list[dict]:
    payload = yaml.safe_load(path.read_text()) or {}
    return [item for item in payload.get("employers") or [] if item.get("slug")]


def _greenhouse(client, employer: dict) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{employer['slug']}/jobs"
    response = client.get(url, params={"content": "true"})
    response.raise_for_status()
    jobs = []
    for item in response.json().get("jobs") or []:
        location = (item.get("location") or {}).get("name") or ""
        if in_netherlands(location):
            jobs.append(
                {
                    "url": item.get("absolute_url"),
                    "title": item.get("title"),
                    "location": location,
                    "description": strip_html(unescape(item.get("content") or "")),
                    "posted_at": item.get("first_published") or item.get("updated_at") or "",
                }
            )
    return jobs


def _recruitee(client, employer: dict) -> list[dict]:
    response = client.get(f"https://{employer['slug']}.recruitee.com/api/offers/")
    response.raise_for_status()
    jobs = []
    for item in response.json().get("offers") or []:
        location = item.get("location") or item.get("city") or ""
        if in_netherlands(f"{location} {item.get('country') or ''}"):
            jobs.append(
                {
                    "url": item.get("careers_url"),
                    "title": item.get("title"),
                    "location": location,
                    "description": strip_html(
                        f"{item.get('description') or ''} {item.get('requirements') or ''}"
                    ),
                    "posted_at": item.get("published_at") or item.get("created_at") or "",
                }
            )
    return jobs


SYSTEMS = {"greenhouse": _greenhouse, "recruitee": _recruitee}


def _scan_ats(client, source, entry, summary, limit_per_source=None) -> None:
    employers = load_employers()
    problems: list[str] = []
    for employer in employers:
        reader = SYSTEMS.get(employer.get("system"))
        if reader is None:
            problems.append(employer["slug"])
            continue
        try:
            found = reader(client, employer)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:  # A removed board simply has no jobs.
                problems.append(employer["slug"])
            continue
        except (httpx.HTTPError, ValueError):
            problems.append(employer["slug"])
            continue
        for item in found[: limit_per_source or len(found)]:
            if not item["url"] or not item["title"]:
                continue
            job = make_job(
                source["id"],
                item["url"],
                item["title"],
                company=employer["name"],
                location=item["location"],
                description=item["description"],
                posted_at=str(item["posted_at"]),
            )
            _, created = store.upsert_job(job)
            _tally(entry, summary, created)
        time.sleep(0.5)
    if problems:
        # A failed board must not make its vacancies look expired.
        raise RuntimeError("Niet bereikbaar: " + ", ".join(problems)[:200])
    store.update_source_status(source["id"], "ok")


scan = _scan_ats


KINDS = ("ats-registry",)
