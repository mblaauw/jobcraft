import json

import httpx
import pytest

from app import store
from app.adapters import eures
from tests.test_jobs_page import client  # noqa: F401
from tests.test_source_http import transport


def _detail(title, town, own_page="", country="nl"):
    return {
        "creationDate": 1793000000000,
        "jvProfiles": {
            "nl": {
                "title": title,
                "description": "<p>" + "Je werkt in een fijn team aan mooie projecten. " * 20 + "</p>",
                "positionScheduleCodes": ["parttime"],
                "locations": [{"countryCode": country, "cityName": town.upper()}],
                "personContacts": [{"communications": {"webProfiles": [{"uri": own_page}] if own_page else []}}],
            }
        },
    }


class FakeEures:
    def __init__(self, items, details):
        self.items, self.details, self.asked, self.searched = items, details, [], []

    def post_json(self, url, payload):
        self.searched.append(payload)
        items = self.items if payload["page"] == 1 else []
        return httpx.Response(200, json={"numberRecords": len(items), "jvs": items}, request=httpx.Request("POST", url))

    def get(self, url, **kwargs):
        self.asked.append(url)
        job_id = url.split("/jv/id/")[1].split("?")[0]
        return httpx.Response(200, json=self.details[job_id], request=httpx.Request("GET", url))


def test_eures_reads_dutch_vacancies_once_and_skips_ones_another_source_has(client):  # noqa: F811
    store.sync_sources([{"id": "eures", "kind": "eures-json"}, {"id": "bureau", "kind": "sitemap-jsonld"}])
    store.upsert_job({"source_id": "bureau", "url": "https://bureau.test/vacatures/7", "title": "Planner", "description": "x" * 500})
    details = {
        "a": _detail("Beleidsadviseur", "Utrecht"),
        "b": _detail("Planner", "Almere", own_page="https://bureau.test/vacatures/7"),
        "c": _detail("Monteur", "Gent", country="be"),
    }
    fake = FakeEures([{"id": key} for key in details], details)
    source = {"id": "eures", "location_codes": ["nl"], "pages": 3}
    eures._scan_eures(fake, source, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    assert fake.searched[0]["locationCodes"] == ["nl"] and fake.searched[0]["sortSearch"] == "MOST_RECENT"
    with store.connect() as con:
        rows = con.execute("SELECT * FROM jobs WHERE source_id='eures'").fetchall()
    assert [row["title"] for row in rows] == ["Beleidsadviseur"]  # Planner is the bureau's, Gent is abroad
    job = rows[0]
    assert job["url"] == eures.PORTAL.format(id="a") and job["location"] == "Utrecht" and job["hours"] == "Parttime"
    assert "fijn team" in job["description"] and "<p>" not in job["description"]
    # Next round: nothing is read again, and the known vacancy stays current.
    fake.asked.clear()
    entry = {"items": 0, "new": 0, "updated": 0}
    eures._scan_eures(fake, source, entry, {"new": 0, "updated": 0})
    assert fake.asked == [] and entry["updated"] == 1


def test_a_round_reads_only_its_cap_of_new_vacancies(client):  # noqa: F811
    store.sync_sources([{"id": "eures", "kind": "eures-json"}])
    details = {f"j{n}": _detail(f"Functie {n}", "Delft") for n in range(5)}
    fake = FakeEures([{"id": key} for key in details], details)
    eures._scan_eures(fake, {"id": "eures", "detail_cap": 2}, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    assert len(fake.asked) == 2
    eures._scan_eures(fake, {"id": "eures", "detail_cap": 2}, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    assert len(fake.asked) == 4  # the next round takes the next two


def test_post_json_obeys_robots_and_sends_the_payload(tmp_path):
    seen = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\nCrawl-delay: 10")
        seen.append((request.method, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    polite, clock = transport(handler, tmp_path)
    assert polite.post_json("https://api.test/search", {"page": 1}).json() == {"ok": True}
    polite.post_json("https://api.test/search", {"page": 2})
    assert seen == [("POST", {"page": 1}), ("POST", {"page": 2})] and 10 in clock.waits
    with pytest.raises(httpx.RequestError, match="disallows"):
        polite.post_json("https://api.test/private/search", {})
