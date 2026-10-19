from __future__ import annotations

from xml.etree import ElementTree

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
        "getro-html",  # documented placeholder, disabled until the adapter exists
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
