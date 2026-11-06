"""Hours filter, opt-in test account and the broader source catalogue."""

import json

import httpx
import yaml

from app import auth, config, sources, store
from app.adapters.fondsen import _scan_fondsen
from app.adapters.html_listing import _scan_html_listing
from app.adapters.jsonld import _scan_sitemap_jsonld
from tests.test_jobs_page import client, soup  # noqa: F401
from tests.test_phases_6_7 import add, titles


def test_hours_filter_reads_the_words_a_vacancy_uses(client):  # noqa: F811
    add("Deeltijd", "Een baan van 24 uur per week")
    add("Voltijd", "Dit is een functie voor 36 uur")
    add("English", "Full position, 40 hours a week")
    add("Stil", "Niets over tijd")
    assert titles(client.get("/jobs?uren=tot24&within=0")) == ["Deeltijd"]
    assert titles(client.get("/jobs?uren=32-40&within=0")) == ["English", "Voltijd"]
    assert len(titles(client.get("/jobs?uren=onzin&within=0"))) == 4


def test_no_automatic_member_account_unless_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "APP_USER", "owner")
    monkeypatch.setattr(config, "APP_PASSWORD", "owner-password")
    monkeypatch.delenv("BOOTSTRAP_TEST_USER", raising=False)
    store.init_db()
    auth.init_auth()
    with store.connect() as con:
        names = {row["username"] for row in con.execute("SELECT username FROM users")}
    assert "test-user" not in names and "admin" in names
    monkeypatch.setenv("BOOTSTRAP_TEST_USER", "1")
    auth.init_auth()
    with store.connect() as con:
        names = {row["username"] for row in con.execute("SELECT username FROM users")}
    assert "test-user" in names


def test_new_sources_respect_their_review():
    seed = {item["id"]: item for item in yaml.safe_load(sources.SEED_FILE.read_text())["sources"]}
    assert seed["academictransfer"]["enabled"] is True
    assert seed["academictransfer"]["kind"] == "sitemap-jsonld"
    # A proof of concept since 2026-11-06; the note keeps the terms for the agreement.
    assert seed["onderwijsvacatures"]["enabled"] is True and "afspraak nodig" in seed["onderwijsvacatures"]["notes"]


class Pages:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kwargs):
        return httpx.Response(200, text=self.pages[url], request=httpx.Request("GET", url))


def _page(summary, body, company="Rijk"):
    ld = {
        "@type": "JobPosting",
        "title": "Adviseur",
        "description": summary,
        "hiringOrganization": {"name": company},
    }
    return (
        f'<html><script type="application/ld+json">{json.dumps(ld)}</script>'
        f"<body><h1>Adviseur</h1><main>{body}</main></body></html>"
    )


def _entry():
    return {"status": "ok", "new": 0, "updated": 0, "items": 0}, {"new": 0, "updated": 0}


def test_one_line_summary_gives_way_to_the_page_text(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.adapters.jsonld.time.sleep", lambda _: None)
    long_text = "Dit ga je doen en dit vragen wij. " * 40
    client_pages = Pages(
        {
            "https://w.test/sitemap.xml": "<urlset><loc>https://w.test/vacatures/a</loc></urlset>",
            "https://w.test/vacatures/a": _page("Adviseur in Utrecht", long_text),
        }
    )
    store.sync_sources([{"id": "w", "kind": "sitemap-jsonld"}])
    entry, summary = _entry()
    source = {"id": "w", "sitemap_url": "https://w.test/sitemap.xml"}
    _scan_sitemap_jsonld(client_pages, source, entry, summary)
    with store.connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE url='https://w.test/vacatures/a'").fetchone()
    assert "dit vragen wij" in row["description"]
    assert row["company"] == "Rijk"


def test_a_board_without_structured_data_gives_title_text_and_place_from_its_page(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.adapters.jsonld.time.sleep", lambda _: None)
    body = "<p>" + "Je geeft les aan groep 7 en werkt samen met een duo. " * 20 + "</p>"
    client_pages = Pages(
        {
            "https://m.test/sitemap.xml": "<urlset><loc>https://m.test/vacature/1/leerkracht</loc></urlset>",
            "https://m.test/vacature/1/leerkracht": (
                "<html><head><title>Vacature Leerkracht groep 7 - De Bussel - Kaatsheuvel</title></head>"
                f"<body><h1>Leerkracht groep 7</h1><main>{body}</main></body></html>"
            ),
        }
    )
    source = {
        "id": "m",
        "sitemap_url": "https://m.test/sitemap.xml",
        "html_fallback": True,
        "title_pattern": "^Vacature .+ - (?P<company>(?:(?! - ).)+) - (?P<place>(?:(?! - ).)+)$",
    }
    store.sync_sources([{"id": "m", "kind": "sitemap-jsonld"}])
    entry, summary = _entry()
    _scan_sitemap_jsonld(client_pages, source, entry, summary)
    _scan_sitemap_jsonld(client_pages, {**source, "id": "m2", "html_fallback": False}, *_entry())
    with store.connect() as con:
        rows = con.execute("SELECT * FROM jobs WHERE source_id IN ('m', 'm2')").fetchall()
    assert len(rows) == 1  # without html_fallback a page without JobPosting is left alone
    row = rows[0]
    assert (row["title"], row["company"], row["location"]) == ("Leerkracht groep 7", "De Bussel", "Kaatsheuvel")
    assert "groep 7 en werkt samen" in row["description"]


def test_a_plain_text_address_or_the_source_default_gives_the_place(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.adapters.jsonld.time.sleep", lambda _: None)
    text = "Je begeleidt collega's op je eigen locatie. Je werkt samen met een collega. " * 10

    def page(address):
        ld = {"@type": "JobPosting", "title": "Verpleegkundige", "description": text}
        if address:
            ld["jobLocation"] = {"@type": "Place", "address": address}
        return f'<html><script type="application/ld+json">{json.dumps(ld)}</script><body></body></html>'

    client_pages = Pages(
        {
            "https://z.test/sitemap.xml": "<urlset><loc>https://z.test/vacatures/1</loc><loc>https://z.test/vacatures/2</loc></urlset>",
            "https://z.test/vacatures/1": page("Wognum"),
            "https://z.test/vacatures/2": page(None),
        }
    )
    store.sync_sources([{"id": "z", "kind": "sitemap-jsonld"}])
    entry, summary = _entry()
    _scan_sitemap_jsonld(client_pages, {"id": "z", "sitemap_url": "https://z.test/sitemap.xml", "default_location": "Hoorn"}, entry, summary)
    with store.connect() as con:
        places = {row["url"][-1]: row["location"] for row in con.execute("SELECT url, location FROM jobs WHERE source_id='z'")}
    assert places == {"1": "Wognum", "2": "Hoorn"}  # never the village Een from "met een collega"


def test_rich_structured_text_is_kept(client, monkeypatch):  # noqa: F811
    from app.adapters.common import best_description

    rich = "Volledige tekst. " * 40
    assert best_description(rich, "<main>menu</main>") == rich
    assert best_description("Kort", "<main></main>") == "Kort"


def test_fondsen_finds_both_link_forms(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.adapters.fondsen.time.sleep", lambda _: None)
    pages = Pages(
        {
            "https://f.test/vacatures/": (
                '<a href="/vacature/oud">a</a><a href="/vacatures/nieuw">b</a>'
                '<a href="/vacatures">alles</a><a href="/vacature-plaatsen">c</a>'
            ),
            "https://f.test/vacature/oud": "<h1>Oud</h1><p>Tekst</p>",
            "https://f.test/vacatures/nieuw": "<h1>Nieuw</h1><p>Tekst</p>",
        }
    )
    store.sync_sources([{"id": "f", "kind": "html-fondsen"}])
    entry, summary = _entry()
    _scan_fondsen(pages, {"id": "f", "base_url": "https://f.test", "path": "/vacatures/"}, entry, summary)
    assert entry["items"] == 2


def test_board_naming_itself_is_not_the_employer(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.adapters.html_listing.time.sleep", lambda _: None)
    pages = Pages(
        {
            "https://b.test/vacatures": '<a href="/vacatures/hovenier">x</a>',
            "https://b.test/vacatures/hovenier": _page("Hovenier", "Tekst", company="Baneninhetgroen"),
        }
    )
    store.sync_sources([{"id": "b", "kind": "html-listing"}])
    entry, summary = _entry()
    source = {
        "id": "b",
        "base_url": "https://b.test",
        "listing_url": "https://b.test/vacatures",
        "link_pattern": "/vacatures/[a-z]+$",
        "site_name": "Baneninhetgroen",
    }
    _scan_html_listing(pages, source, entry, summary)
    with store.connect() as con:
        assert con.execute("SELECT company FROM jobs").fetchone()["company"] == ""
