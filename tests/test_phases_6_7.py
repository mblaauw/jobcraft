"""List filters, in-place votes, compression and the page order on Account."""

from datetime import UTC, datetime, timedelta

from app import store
from tests.test_jobs_page import client, soup  # noqa: F401


def add(title, description, posted=None):
    job_id, _ = store.upsert_job(
        {
            "source_id": "test",
            "url": f"https://example.test/{title}",
            "title": title,
            "company": title,
            "description": description,
            "location": "unknown",
            "posted_at": posted or datetime.now(UTC).isoformat(),
            "content_hash": title,
        }
    )
    return job_id


def titles(response):
    return sorted(el.get_text() for el in soup(response).select(".vacancy-main h3 a"))


def test_filters_keep_only_vacancies_that_say_so(client):  # noqa: F811
    add("Thuis", "Deels thuis werken in een hybride team")
    add("Vast", "Een vast contract bij ons")
    add("Betaald", "Salaris € 4.000 per maand")
    add("Stil", "Verder niets")
    assert titles(client.get("/jobs?remote=remote&within=0")) == ["Thuis"]
    assert titles(client.get("/jobs?contract=vast&within=0")) == ["Vast"]
    assert titles(client.get("/jobs?salaris=salaris&within=0")) == ["Betaald"]
    assert len(titles(client.get("/jobs?within=0"))) == 4


def test_english_vacancies_pass_the_same_filters(client):  # noqa: F811
    add("Permanent", "This is a permanent position in Amsterdam")
    add("Temp", "A fixed-term role")
    assert titles(client.get("/jobs?contract=vast&within=0")) == ["Permanent"]
    assert titles(client.get("/jobs?contract=tijdelijk&within=0")) == ["Temp"]


def test_date_filter_uses_publication_date(client):  # noqa: F811
    add("Nieuw", "x")
    add("Oud", "x", (datetime.now(UTC) - timedelta(days=20)).isoformat())
    assert titles(client.get("/jobs?dagen=7&within=0")) == ["Nieuw"]
    assert titles(client.get("/jobs?dagen=30&within=0")) == ["Nieuw", "Oud"]


def test_counts_chips_and_links_follow_the_filters(client):  # noqa: F811
    add("Vast", "Een vast contract")
    add("Stil", "Niets")
    html = soup(client.get("/jobs?contract=vast&within=0"))
    assert "1 resultaat" in html.get_text()
    assert "Contract: vast" in html.select_one(".jobs-active-filters").get_text()
    for tab in html.select(".status-tabs a"):
        assert "contract=vast" in tab["href"]
    assert html.select_one('#job-contract option[value="vast"]').has_attr("selected")


def test_unknown_filter_values_are_ignored(client):  # noqa: F811
    add("Stil", "Niets")
    assert titles(client.get("/jobs?contract=x'%20OR%201=1&dagen=999&remote=x&within=0")) == ["Stil"]


def test_vote_script_is_loaded_and_form_still_works_without_it(client):  # noqa: F811
    job_id = add("Stil", "Niets")
    html = soup(client.get("/jobs?within=0"))
    assert html.select_one('script[src="/static/votes.js"]')
    response = client.post(f"/jobs/{job_id}/vote", data={"vote": "up"}, follow_redirects=False)
    assert response.status_code == 303


def test_large_pages_are_compressed_but_streams_are_not(client):  # noqa: F811
    for number in range(30):
        add(f"Baan {number}", "Tekst")
    response = client.get("/jobs?within=0", headers={"accept-encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"
    assert soup(response).select(".vacancy-row")


def test_account_lists_appearance_last(client):  # noqa: F811
    text = client.get("/account").text
    assert text.index('id="data-title"') < text.index('id="theme-title"')
    assert text.index('id="password-title"') < text.index('id="theme-title"')
