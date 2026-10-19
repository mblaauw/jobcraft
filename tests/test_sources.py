from __future__ import annotations

from app.sources import normalize_wp_item, split_company, strip_html


def test_strip_html_handles_rendered_dicts():
    assert strip_html({"rendered": "<p>Hallo <b>wereld</b></p>"}) == "Hallo wereld"


def test_split_company_only_for_short_prefixes():
    assert split_company("De Voorkamer: Voorzitter") == ("De Voorkamer", "Voorzitter")
    assert split_company("Een hele lange titel zonder duidelijke organisatie: rol") == (
        "",
        "Een hele lange titel zonder duidelijke organisatie: rol",
    )


def test_normalize_wp_item_without_page_enrichment():
    source = {"id": "culturele-vacatures", "kind": "wp-posts"}
    item = {
        "date": "2026-10-17T10:10:02",
        "link": "https://example.org/2026/10/voorzitter/",
        "title": {"rendered": "De Voorkamer: Voorzitter"},
        "content": {"rendered": "<p>Utrecht zoekt een voorzitter voor 2 tot 4 uur.</p>"},
    }
    job = normalize_wp_item(source, item)
    assert job["title"] == "Voorzitter"
    assert job["company"] == "De Voorkamer"
    assert job["source_id"] == "culturele-vacatures"
    assert job["description"].startswith("Utrecht zoekt")
    assert len(job["content_hash"]) == 64


def test_normalize_rejects_items_without_link_or_title():
    source = {"id": "x", "kind": "wp-posts"}
    assert normalize_wp_item(source, {"link": "", "title": {"rendered": "x"}}) is None
    assert normalize_wp_item(source, {"link": "https://x", "title": {"rendered": ""}}) is None
