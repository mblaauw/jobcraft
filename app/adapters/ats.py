"""Employer registry: public Greenhouse, Recruitee and Personio boards, one request per employer.

Adding an employer is one line in `seeds/employers.yaml`. Greenhouse and Recruitee
return full vacancy text in one response. Personio's XML feed often leaves the text
out; its server-rendered job pages fill it in, a capped number per board per round.
"""

from __future__ import annotations

import re
import time
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

import httpx
import yaml

from .. import geo, store
from .common import (
    SHORT_SUMMARY,
    _skip_recent_detail,
    _tally,
    best_description,
    make_job,
    safe_detail_get,
    strip_html,
)

EMPLOYERS_FILE = Path(__file__).resolve().parents[2] / "seeds" / "employers.yaml"
_NETHERLANDS = re.compile(r"\b(?:netherlands|nederland|the hague)\b|, ?NL\b|\bNL-", re.I)
_ELSEWHERE = re.compile(
    r"\b(?:germany|deutschland|belgium|france|spain|italy|ireland|poland|portugal|sweden|denmark|"
    r"austria|switzerland|united kingdom|usa|united states|canada|india|singapore|australia|slovakia|"
    r"czechia|czech republic|hungary|romania|greece|norway|finland)\b",
    re.I,
)


def in_netherlands(location: str | None) -> bool:
    text = location or ""
    if _NETHERLANDS.search(text):
        return True
    return not _ELSEWHERE.search(text) and geo._find_place(text) in geo.CORE_PLACES


def dutch_office(office: str) -> bool:
    """An office on a single employer's board: any Dutch place counts, not only the big towns."""
    return in_netherlands(office) or (not _ELSEWHERE.search(office) and geo._find_place(office) is not None)


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


PERSONIO_SCHEDULE = {"full-time": "Fulltime", "part-time": "Parttime", "full-or-part-time": "Fulltime of parttime"}
PERSONIO_CONTRACT = {
    "permanent": "vast",
    "temporary": "tijdelijk",
    "intern": "stage",
    "trainee": "traineeship",
    "freelance": "freelance",
    "working_student": "werkstudent",
}
DETAIL_CAP = 40


def _personio_hours(schedule: str, contract: str) -> str:
    parts = [PERSONIO_SCHEDULE.get(schedule, schedule), PERSONIO_CONTRACT.get(contract, contract)]
    return " · ".join(part for part in parts if part)


def _personio(client, employer: dict) -> list[dict]:
    # The tld in the registry line is the one that answers without a redirect, so a job's
    # URL stays the same from round to round.
    host = f"https://{employer['slug']}.jobs.personio.{employer.get('tld') or 'com'}"
    response = client.get(f"{host}/xml")
    response.raise_for_status()
    if b"<!DOCTYPE" in response.content[:2000]:
        raise ValueError("Personio feed with a DOCTYPE")  # no entity tricks from a feed
    jobs = []
    for item in ElementTree.fromstring(response.content).findall("position"):
        offices = [item.findtext("office") or "", *(office.text or "" for office in item.findall("additionalOffices/office"))]
        offices = [office.strip() for office in offices if office.strip()]
        if not any(dutch_office(office) for office in offices):
            continue
        parts = item.findall("jobDescriptions/jobDescription")
        description = "\n\n".join(
            f"{part.findtext('name') or ''}\n{strip_html(part.findtext('value') or '')}".strip() for part in parts
        ).strip()
        jobs.append(
            {
                "url": f"{host}/job/{item.findtext('id')}" if item.findtext("id") else "",
                "title": item.findtext("name"),
                "location": ", ".join(offices),
                "description": description,
                "posted_at": item.findtext("createdAt") or "",
                "hours": _personio_hours(item.findtext("schedule") or "", item.findtext("employmentType") or ""),
                "details": True,
            }
        )
    return jobs


SYSTEMS = {"greenhouse": _greenhouse, "recruitee": _recruitee, "personio": _personio}


def _details(client, item: dict, entry: dict, summary: dict, budget: list[int]) -> str | None:
    """The job page's text when the feed had too little; None when a recent fetch made it known."""
    description = item["description"]
    if not item.get("details") or len(description) >= SHORT_SUMMARY:
        return description
    if _skip_recent_detail(item["url"], entry, summary):
        return None
    if budget[0] <= 0:
        return description  # the rest of a new board's pages follow in later rounds
    budget[0] -= 1
    page = safe_detail_get(client, item["url"], entry)
    time.sleep(0.4)
    if page is None or page.status_code >= 400:
        return description
    return best_description(description, page.text)


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
        budget = [int(employer.get("detail_cap") or DETAIL_CAP)]
        try:
            for item in found[: limit_per_source or len(found)]:
                if not item["url"] or not item["title"]:
                    continue
                description = _details(client, item, entry, summary, budget)
                if description is None:
                    continue
                job = make_job(
                    source["id"],
                    item["url"],
                    item["title"],
                    company=employer["name"],
                    location=item["location"],
                    hours=item.get("hours") or "",
                    description=description,
                    posted_at=str(item["posted_at"]),
                )
                _, created = store.upsert_job(job)
                _tally(entry, summary, created)
        except httpx.HTTPError:
            problems.append(employer["slug"])  # its job pages kept failing; other boards go on
        time.sleep(0.5)
    if problems:
        # A failed board must not make its vacancies look expired.
        raise RuntimeError("Niet bereikbaar: " + ", ".join(problems)[:200])
    store.update_source_status(source["id"], "ok")


scan = _scan_ats


KINDS = ("ats-registry",)
