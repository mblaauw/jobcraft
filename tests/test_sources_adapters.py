from __future__ import annotations

from xml.etree import ElementTree

import pytest
from test_jobs_page import client  # noqa: F401

from app import sources

SAMPLE_FEED_ITEM = """<source><items><item>
<unique_id>67230</unique_id>
<title><![CDATA[Senior Tactisch Inkoper]]></title>
<business_unit>Landmacht</business_unit>
<url>https://werkenbijdefensie.nl/vacatures/niet-militaire-vacatures/senior-tactisch-inkoper-e67230</url>
<short_intro><![CDATA[Korte intro voor de functie.]]></short_intro>
<additionals>
  <salary><![CDATA[&euro;4.750 ~ &euro;6.550]]></salary>
  <days_off>23 ~ 30 vrije dagen per jaar</days_off>
  <driver_license_provided>Ja</driver_license_provided>
</additionals>
<work_areas><work_area><id>34</id><title><![CDATA[Inkoop / verkoop]]></title></work_area></work_areas>
</item></items></source>"""


def test_normalize_defensie_maps_feed_fields():
    item = ElementTree.fromstring(SAMPLE_FEED_ITEM).find(".//item")
    assert item is not None
    job = sources.normalize_defensie({"id": "defensie-civilian"}, item)
    assert job is not None
    assert job["url"].endswith("senior-tactisch-inkoper-e67230")
    assert job["company"] == "Defensie · Landmacht"
    assert "niet-militair" in job["description"]
    assert "4.750" in job["description"]
    assert "Inkoop / verkoop" in job["description"]
    assert "Rijbewijs via Defensie: Ja" in job["description"]


def test_normalize_defensie_requires_title_and_url():
    missing_url = ElementTree.fromstring("<item><title>Zonder url</title></item>")
    assert sources.normalize_defensie({"id": "d"}, missing_url) is None
    missing_title = ElementTree.fromstring("<item><url>https://x.test/job</url></item>")
    assert sources.normalize_defensie({"id": "d"}, missing_title) is None


def test_defensie_details_extracts_body_and_location():
    payloads = [
        {
            "data": {
                "type": "VacancyJobDescriptionParagraph",
                "body": "<p>" + "Taken en verantwoordelijkheden. " * 5 + "</p>",
            }
        },
        {"data": {"location": "Den Haag"}},
    ]
    description, location = sources._defensie_details(payloads)
    assert "Taken en verantwoordelijkheden." in description
    assert location == "Den Haag"


def test_configured_sources_use_known_adapters():
    known = {
        "wp-posts",
        "wp-job-listings",
        "html-fondsen",
        "html-listing",
        "sitemap-jsonld",
        "rss",
        "exenzo-json",
        "defensie-cms",
        "hn-resource",
        "local-json",
        "ats-registry",
        "getro-html",
        "eures-json",
        "manual",
        "browser-import",
    }
    sources_list = sources.load_sources()
    assert sources_list, "no sources configured"
    for source in sources_list:
        assert source.get("kind") in known, f"unknown adapter for {source.get('id')}"
        assert source.get("id")


SAMPLE_HN_CARD = """
<ul>
<li class="vacancy-list__item" data-govan-component="search-result-item">
  <section class="vacancy" data-baanpleinId="50040-20261012-1562">
    <h2 class="vacancy__title"><a data-govan-action="view-card" href="/vacatures/ai-expert-JTIS-2026-8040">AI Expert</a></h2>
    <p class="vacancy__employer">Justis</p>
    <ul class="job-short-info--width">
      <li class="job-short-info__item"><span class="job-short-info__icon" title="Locatie"></span><span class="job-short-info__value">Den Haag (Rijnstraat)</span></li>
      <li class="job-short-info__item"><span class="job-short-info__icon" title="Aantal uren"></span><span class="job-short-info__value">32 - 36 uur</span></li>
    </ul>
  </section>
</li>
</ul>
"""


def test_parse_hn_cards_extracts_title_employer_and_labels():
    cards = sources._parse_hn_cards(SAMPLE_HN_CARD)
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == "AI Expert"
    assert card["href"] == "/vacatures/ai-expert-JTIS-2026-8040"
    assert card["employer"] == "Justis"
    assert card["labels"]["Locatie"] == "Den Haag (Rijnstraat)"
    assert card["labels"]["Aantal uren"] == "32 - 36 uur"


def test_getro_board_reads_new_jobs_and_keeps_live_ones_from_expiring(client, monkeypatch):  # noqa: F811
    import json as _json

    import httpx

    from app import store
    from app.adapters import getro

    monkeypatch.setattr(getro.time, "sleep", lambda _: None)
    base = "https://bord.test"

    def page(state):
        return f'<html><script id="__NEXT_DATA__" type="application/json">{_json.dumps({"props": {"pageProps": {"initialState": state}}})}</script></html>'

    def job(slug, status="active", place="The Hague, Netherlands"):
        return {"jobs": {"currentJob": {"title": f"Vacature {slug}", "status": status, "locations": [{"name": place}],
                                        "postedAt": "2026-11-04T13:15:34Z", "organization": {"name": "Oscar", "slug": "oscar"},
                                        "description": "<p>" + "Je bouwt mee aan een circulaire stad. " * 20 + "</p>"}}}

    listing = {"jobs": {"total": 3, "found": [{"slug": s, "organization": {"slug": "oscar"}} for s in ("1-a", "2-b", "3-c")]}}
    pages = {f"{base}/jobs": page(listing), f"{base}/companies/oscar/jobs/1-a": page(job("1-a")),
             f"{base}/companies/oscar/jobs/2-b": page(job("2-b", place="Berlin, Germany")),
             f"{base}/companies/oscar/jobs/3-c": page(job("3-c", status="closed"))}

    asked: list[str] = []

    class Site:
        def get(self, url, **kwargs):
            asked.append(url)
            request = httpx.Request("GET", url)
            return httpx.Response(200, text=pages[url], request=request) if url in pages else httpx.Response(404, request=request)

    source = {"id": "impact", "kind": "getro-html", "base_url": base}
    store.sync_sources([source])
    getro._scan_getro(Site(), source, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    with store.connect() as con:
        rows = con.execute("SELECT url, title, company, location, description FROM jobs").fetchall()
    assert [(row["title"], row["company"], row["location"]) for row in rows] == [("Vacature 1-a", "Oscar", "The Hague")]
    assert "circulaire stad" in rows[0]["description"]
    # It drops off the list, but its own page says it is still open: it is read again, not expired.
    pages[f"{base}/jobs"] = page({"jobs": {"total": 0, "found": []}})
    with store.connect() as con:
        con.execute("UPDATE meta SET value='2000-01-01T00:00:00+00:00' WHERE key LIKE 'detail_fetched:%'")
        con.execute("UPDATE jobs SET last_seen='2000-01-01T00:00:00+00:00'")
    asked.clear()
    getro._scan_getro(Site(), source, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    assert f"{base}/companies/oscar/jobs/1-a" in asked
    with store.connect() as con:
        assert con.execute("SELECT last_seen FROM jobs").fetchone()[0] > "2026"
    # A list that says it has jobs but names none means the page changed: report it.
    pages[f"{base}/jobs"] = page({"jobs": {"total": 5, "found": []}})
    with pytest.raises(RuntimeError):
        getro._scan_getro(Site(), source, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    pages[f"{base}/jobs"] = "<html>geen data</html>"
    with pytest.raises(ValueError):
        getro._scan_getro(Site(), source, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
