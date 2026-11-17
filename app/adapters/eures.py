"""EURES, the European job portal: vacancies in the Netherlands, most of them from werk.nl.

Proof of concept for own use: EURES's terms keep its search API for partner organisations.
Each new vacancy is read from its own detail endpoint for the full text, the town and the
employer's own page. A vacancy whose own page another source already brought in is
skipped, so the same job does not show twice. robots.txt asks 10 seconds between requests,
so a round reads a capped number of new vacancies; vacancies read before stay current
while the search still lists them.

The search's "newest first" and its publication period go by the last change, and werk.nl
touches its whole stock every night, so the top of the list is mostly old vacancies. A
round therefore walks its pages first and spends the reads on the vacancies created most
recently, never on ones older than `max_age_days`. The search serves at most the first
10,000 results (200 pages of 50); asking past that is an error."""

from __future__ import annotations

import time

from .. import store
from .common import _tally, make_job, safe_detail_get, strip_html

API = "https://europa.eu/eures/api/jv-searchengine/public"
PORTAL = "https://europa.eu/eures/portal/jv-se/jv-details/{id}?lang=nl"
KNOWN_FOR = 30 * 86400  # a vacancy read within this time is only marked as still listed
MAX_AGE_DAYS = 21  # older vacancies are not read: few are still open, and they crowd out new ones
STILL_LISTED = 365 * 86400  # one read that long ago is kept current while the search lists it
MAX_RESULTS = 10_000  # the search answers 400 "Too many results" past this
SCHEDULES = {"fulltime": "Fulltime", "parttime": "Parttime", "flextime": "Flexibel"}


def search_body(source: dict, page: int) -> dict:
    return {
        "resultsPerPage": int(source.get("per_page", 50)),
        "page": page,
        "sortSearch": "MOST_RECENT",
        "keywords": [],
        "publicationPeriod": source.get("period", "LAST_THREE_DAYS"),
        "occupationUris": [],
        "skillUris": [],
        "requiredExperienceCodes": [],
        "positionScheduleCodes": [],
        "sectorCodes": [],
        "educationAndQualificationLevelCodes": [],
        "positionOfferingCodes": [],
        "locationCodes": source.get("location_codes", ["nl"]),
        "euresFlagCodes": [],
        "otherBenefitsCodes": [],
        "requiredLanguages": [],
        "minNumberPost": None,
        "sessionId": "jobcraft",
    }


def posting(detail: dict) -> dict | None:
    """Title, text, town, hours and the employer's own page from a detail answer."""
    profiles = detail.get("jvProfiles") or {}
    profile = profiles.get("nl") or next(iter(profiles.values()), None)
    if not isinstance(profile, dict) or not profile.get("title"):
        return None
    towns = [
        (place.get("cityName") or "").strip().title()
        for place in profile.get("locations") or []
        if (place.get("countryCode") or "").lower() == "nl"
    ]
    if profile.get("locations") and not any(towns):
        return None  # listed in the Netherlands, but the vacancy itself is elsewhere
    pages = [
        web.get("uri")
        for contact in profile.get("personContacts") or []
        for web in (contact.get("communications") or {}).get("webProfiles") or []
        if (web.get("uri") or "").startswith("http")
    ]
    created = detail.get("creationDate")
    return {
        "title": profile["title"].strip(),
        "description": strip_html(profile.get("description") or ""),
        "location": ", ".join(dict.fromkeys(town for town in towns if town)),
        "hours": " · ".join(SCHEDULES[code] for code in profile.get("positionScheduleCodes") or [] if code in SCHEDULES),
        "posted_at": time.strftime("%Y-%m-%d", time.gmtime(created / 1000)) if isinstance(created, int) else "",
        "own_page": pages[0] if pages else "",
    }


def _scan_eures(client, source, entry, summary, limit_per_source=None) -> None:
    budget = limit_per_source or int(source.get("detail_cap", 300))
    oldest_ms = (time.time() - float(source.get("max_age_days", MAX_AGE_DAYS)) * 86400) * 1000
    fresh: dict[str, float] = {}  # id -> creation time, for the vacancies worth a read
    per_page = max(1, int(source.get("per_page", 50)))
    last_page = max(1, min(int(source.get("pages", 20)), MAX_RESULTS // per_page))
    for page in range(1, last_page + 1):
        response = client.post_json(f"{API}/jv-search/search", search_body(source, page))
        response.raise_for_status()
        items = response.json().get("jvs") or []
        if not items:
            break
        for item in items:
            url = PORTAL.format(id=item["id"])
            if store.touch_recent_detail(url, KNOWN_FOR):
                _tally(entry, summary, False)
                continue
            if store.fetched_within(url, KNOWN_FOR):
                continue  # read before and left out: elsewhere, or a duplicate
            created = item.get("creationDate")
            if not isinstance(created, int | float):
                created = 0  # no date given: read only when nothing dated is left
            elif created < oldest_ms:
                # One we read long ago stays current without a new read; the rest is old stock.
                if store.touch_recent_detail(url, STILL_LISTED):
                    _tally(entry, summary, False)
                else:
                    entry["too_old"] = entry.get("too_old", 0) + 1
                continue
            fresh.setdefault(item["id"], created)
    # Newest first; whatever is left over waits for a later round.
    for job_id in sorted(fresh, key=fresh.get, reverse=True)[:budget]:
        url = PORTAL.format(id=job_id)
        answer = safe_detail_get(client, f"{API}/jv/id/{job_id}?requestLang=nl", entry)
        if answer is None or answer.status_code >= 400:
            continue
        store.record_detail_fetch(url)  # later rounds look the vacancy up by its portal URL
        found = posting(answer.json())
        if found is None or (found["own_page"] and store.known_job_urls([found["own_page"]])):
            continue  # not in the Netherlands, or another source has its own page already
        job = make_job(
            source["id"],
            url,
            found["title"],
            location=found["location"],
            hours=found["hours"],
            description=found["description"],
            posted_at=found["posted_at"],
        )
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
    store.update_source_status(source["id"], "ok")


scan = _scan_eures


KINDS = ("eures-json",)
