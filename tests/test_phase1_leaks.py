"""Phase 1 contracts: SQL paging, resilient sources, automatic assessment, friendly errors."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import geo, matching, sources, store, systemone, tenancy, workflows
from app.adapters.common import DETAIL_FAILURE_LIMIT, safe_detail_get
from tests.test_jobs_page import add_job, client, soup  # noqa: F401


def test_travel_filter_counts_and_pages_are_computed_in_sql(client):  # noqa: F811
    for number in range(25):
        add_job(f"Dichtbij {number}", rank=number)
    add_job("Ver weg", location="far", rank=99)
    html = soup(client.get("/jobs?per_page=10&page=3"))
    assert "25 resultaten" in html.get_text()
    assert "1 buiten je reisbereik verborgen" in html.get_text()
    assert [el.get_text() for el in html.select(".vacancy-main h3")][:2] == [
        "Dichtbij 4",
        "Dichtbij 3",
    ]
    assert "Ver weg" in soup(client.get("/jobs?within=0&per_page=10")).get_text()


def test_travel_refresh_only_recomputes_changed_jobs(client):  # noqa: F811
    job_id = add_job()
    from app import ranking

    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    with tenancy.scope({"id": owner}):
        assert ranking.refresh_travel() == 1
        assert ranking.refresh_travel() == 0
        with store.connect() as con:
            con.execute("UPDATE jobs SET location='far' WHERE id=?", (job_id,))
        assert ranking.refresh_travel() == 1


def test_sql_and_python_travel_keys_agree(client):  # noqa: F811
    job_id = add_job()
    with store.connect() as con:
        con.execute("UPDATE jobs SET lat=52.37000000000001, lon=4.25 WHERE id=?", (job_id,))
        row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        sql_key = con.execute(
            f"SELECT {store.jobs.TRAVEL_KEY_SQL} AS k FROM jobs j WHERE j.id=?", ("home", job_id)
        ).fetchone()["k"]
    assert sql_key == store.travel_key("home", row)


def test_unmapped_place_is_still_a_location():
    home = {"label": "Amsterdam", "lat": 52.37, "lon": 4.9, "bike_minutes": 30, "transit_minutes": 35}
    known = geo.assess("Nijkerk", home_settings=home)
    assert known["source"] == "place"
    assert known["place"] == "Nijkerk"
    unknown = geo.assess("Atlantis", home_settings=home)
    assert unknown["in_range"] is None
    assert unknown["label"] == "reistijd onbekend"
    assert geo.assess("", home_settings=home)["label"] == "locatie onbekend"


def test_place_lookup_prefers_the_longest_name():
    assert geo._find_place("Bergen op Zoom") == "bergen op zoom"
    assert geo._find_place("Amsterdam, Noord-Holland") == "amsterdam"
    assert geo._find_place("Oostenrijk") is None


def test_profile_offers_all_places_and_saves_canonical_label(client):  # noqa: F811
    options = soup(client.get("/profile")).select("#home-places option")
    assert len(options) > 2500
    response = client.post("/profile/home", data={"place": "nijkerk"}, follow_redirects=False)
    assert response.headers["location"].endswith("saved=home#reisbereik")


class FakeClient:
    def __init__(self, failing, reason="Robots policy disallows this source URL"):
        self.failing = failing
        self.reason = reason

    def get(self, url, **kwargs):
        if url in self.failing:
            raise httpx.RequestError(self.reason)
        return httpx.Response(200, text="ok")


def test_one_bad_page_skips_only_that_vacancy(client):  # noqa: F811
    entry = {}
    fake = FakeClient({"https://a.test/bad"})
    assert safe_detail_get(fake, "https://a.test/bad", entry) is None
    assert entry["details_skipped"] == 1
    assert "failure_streak" not in entry  # robots refusals never fail a source
    assert safe_detail_get(fake, "https://a.test/good", entry).status_code == 200
    assert entry == {"details_skipped": 1, "failure_streak": 0}


def test_repeated_failures_fail_the_source_so_nothing_expires(client):  # noqa: F811
    entry = {}
    fake = FakeClient({"https://a.test/bad"}, reason="connection reset")
    for _ in range(DETAIL_FAILURE_LIMIT - 1):
        assert safe_detail_get(fake, "https://a.test/bad", entry) is None
    with pytest.raises(httpx.RequestError):
        safe_detail_get(fake, "https://a.test/bad", entry)


def test_seed_revision_updates_settings_but_keeps_on_off_state(client, tmp_path, monkeypatch):  # noqa: F811
    seed = tmp_path / "seed.yaml"
    seed.write_text("sources:\n  - {id: a, kind: rss, per_page: 500, revision: 2, enabled: true}\n")
    monkeypatch.setattr(sources, "SEED_FILE", seed)
    store.save_source_config({"id": "a", "kind": "rss", "per_page": 40}, enabled=False)
    loaded = {item["id"]: item for item in sources.load_sources()}["a"]
    assert loaded["per_page"] == 500
    assert loaded["enabled"] is False
    stored = {item["id"]: item for item in store.load_source_configs()}["a"]
    assert stored["revision"] == 2


def test_catalogue_limits_cover_the_published_vacancies():
    seed = {item["id"]: item for item in __import__("yaml").safe_load(sources.SEED_FILE.read_text())["sources"]}
    assert seed["werkenvoornederland"]["per_page"] >= 1349
    assert seed["gemeentebanen"]["per_page"] >= 566
    assert seed["defensie-civilian"]["per_page"] >= 294


def test_rounds_stop_when_no_progress_and_at_budget(client, monkeypatch):  # noqa: F811
    calls = []

    def fake_match(manager):
        calls.append(1)
        return {"matched": 1 if len(calls) == 1 else 0, "signals": 0, "cost_usd": 0.01, "remaining": 5}

    monkeypatch.setattr(matching, "match_jobs", fake_match)
    workflows._run_match()
    assert len(calls) == 2
    assert store.meta_get("match_status") == "done"

    calls.clear()
    monkeypatch.setattr(workflows, "_budget_reached", lambda: True)
    workflows._run_match()
    assert not calls
    assert store.meta_get("match_status") == "budget"
    assert "matched" in store.meta_get("match_summary")  # the last real round stays visible

    monkeypatch.setattr(workflows, "_budget_reached", lambda: False)
    monkeypatch.setattr(matching, "match_jobs", lambda manager: {"matched": 0, "signals": 0, "failed": 3})
    workflows._run_match()
    assert store.meta_get("match_status").startswith("error")


def test_jev_calls_stop_at_the_daily_ceiling(client, monkeypatch):  # noqa: F811
    add_job()
    monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "1")
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    monkeypatch.setattr(systemone, "signals_key", lambda *a, **k: "key")
    monkeypatch.setattr(systemone, "questions_for", lambda prefs: [])
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    monkeypatch.setattr(matching, "current_digest", lambda *args: "test")
    monkeypatch.setattr(matching.ranking, "rerank_all", lambda *args: None)
    monkeypatch.setattr(matching.ranking, "escalation_candidates", lambda *args: [])
    decided = []
    monkeypatch.setattr(systemone, "decide", lambda *a, **k: decided.append(1) or (_ for _ in ()).throw(RuntimeError()))
    with tenancy.scope({"id": owner}):
        store.record_usage(None, "systemone", 1, 1, 0.0, agent="jev")
        matching.match_jobs(None)
    assert not decided


def test_profile_change_queues_assessment_without_running_it(client, monkeypatch):  # noqa: F811
    from app.routers import profile as routes

    monkeypatch.setattr(routes.profile, "profile_exists", lambda: True)
    response = client.post("/profile/save", data={"content": "x" * 60}, follow_redirects=False)
    assert response.status_code == 303
    with store.connect() as con:
        kinds = [row["kind"] for row in con.execute("SELECT kind FROM tasks").fetchall()]
    assert kinds == ["match"]


def test_browser_errors_are_friendly_and_api_stays_json(client):  # noqa: F811
    page = client.get("/jobs/999999", headers={"accept": "text/html"})
    assert page.status_code == 404
    assert "unknown job" not in page.text
    assert "Deze pagina bestaat niet" in page.text
    assert client.get("/api/sessions/nope/messages", headers={"accept": "text/html"}).json()
    assert client.get("/jobs/999999").json() == {"detail": "unknown job"}


def test_crash_shows_a_page_without_internals(client, monkeypatch):  # noqa: F811
    from app import main

    monkeypatch.setattr(main.store, "count_jobs", lambda: 1 / 0)
    quiet = TestClient(main.app, raise_server_exceptions=False, cookies=client.cookies)
    response = quiet.get("/jobs", headers={"accept": "text/html"})
    assert response.status_code == 500
    assert "ZeroDivisionError" not in response.text
    assert "Er ging aan onze kant iets mis" in response.text


def test_costs_are_not_shown_to_job_seekers(client):  # noqa: F811
    job_id = add_job()
    store.meta_set("match_summary", '{"matched": 1, "signals": 1, "cost_usd": 0.12}')
    for url in ("/jobs", f"/jobs/{job_id}", "/"):
        text = soup(client.get(url)).get_text()
        assert "0,003" not in text and "0.1200" not in text and "Kosten" not in text


def test_document_failure_never_shows_internal_validation_text(client, monkeypatch):  # noqa: F811
    job_id = add_job()
    from app import docs, runtime

    monkeypatch.setattr(runtime, "manager", object())
    monkeypatch.setattr(workflows, "get_manager", lambda: object())
    monkeypatch.setattr(
        docs, "generate_for_job", lambda *a: (_ for _ in ()).throw(ValueError("CV strong_points must be a JSON array"))
    )
    workflows._run_document(job_id, "resume")
    assert "strong_points" not in store.meta_get(f"doc_status:{job_id}")
