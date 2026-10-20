"""Jobs discovery contracts, isolated from personal data and provider calls."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app import auth, config, main, store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "APP_USER", "test")
    monkeypatch.setattr(config, "APP_PASSWORD", "test-password")
    store.init_db()
    auth.init_auth()
    monkeypatch.setattr(main.matching, "current_digest", lambda: "test")
    monkeypatch.setattr(main.ranking, "queue_depth", lambda digest: 0)
    monkeypatch.setattr(
        main.feedback,
        "load",
        lambda: {"counts": {"up": 2, "down": 1}, "liked_keywords": [], "disliked_keywords": []},
    )
    monkeypatch.setattr(main.feedback, "rebuild", lambda: None)
    monkeypatch.setattr(main.ranking, "rerank_all", lambda: None)
    monkeypatch.setattr(
        main.geo, "home", lambda: {"label": "Amsterdam", "bike_minutes": 30, "transit_minutes": 35}
    )
    monkeypatch.setattr(
        main.geo,
        "assess",
        lambda location, *args, **kwargs: {
            "in_range": False if location == "far" else None,
            "label": "Reistijd onbekend",
        },
    )
    # No lifespan: don't start the LLM client or initialise personal files.
    client = TestClient(main.app)
    token = BeautifulSoup(client.get("/login").text, "html.parser").select_one(
        'input[name="_csrf"]'
    )["value"]
    response = client.post(
        "/login",
        data={"username": "test", "password": "test-password", "_csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    token = BeautifulSoup(client.get("/jobs").text, "html.parser").select_one(
        'input[name="_csrf"]'
    )["value"]
    client.headers["x-csrf-token"] = token
    yield client
    client.close()


def add_job(title="Groenmedewerker", location="unknown", date="2026-10-01", rank=61):
    job_id, _ = store.upsert_job(
        {
            "source_id": "test",
            "url": f"https://example.test/{title}",
            "title": title,
            "company": "Testbedrijf",
            "description": "Buiten werken",
            "location": location,
            "hours": "24 uur",
            "posted_at": date,
            "content_hash": title,
        }
    )
    store.set_rank_scores({job_id: rank})
    return job_id


def soup(response):
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def test_cards_scores_and_collapsed_history(client):
    job_id = add_job()
    store.meta_set("match_status", "done")
    store.meta_set(
        "match_summary", '{"matched": 6, "signals": 12, "remaining": 52, "cost_usd": 0.01}'
    )
    html = soup(client.get("/jobs"))
    assert html.select_one(f"article#job-{job_id}")
    score_box = html.select_one(".vacancy-score")
    assert "61" in score_box.get_text()
    assert "/100" in score_box.get_text()
    assert "mid" in score_box["class"]
    assert html.select_one(".vacancy-score-label").get_text() == "Matchscore"
    assert "% zeker" not in html.get_text()
    assert "0 in wachtrij" in html.select_one(".jobs-matching").get_text()
    assert "52 in wachtrij" not in html.get_text()
    assert not html.select("table")
    assert not html.select(".jobs-active-filters a")
    assert all(not detail.has_attr("open") for detail in html.select(".jobs-insights details"))
    assert html.select_one('script[src="/static/jobs.js"]')


def test_task_status_api_is_owner_scoped(client):
    from app import tasks

    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    task_id = tasks.enqueue("scan", owner=owner)
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["kind"] == "scan"
    assert client.get("/api/tasks/999999").status_code == 404


def test_document_status_is_not_cleared_by_get(client):
    job_id = add_job()
    raw = '{"state":"done","kind":"resume","version":1,"finished_at":"2026-10-19T10:00:00+00:00"}'
    store.meta_set(f"doc_status:{job_id}", raw)
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert store.meta_get(f"doc_status:{job_id}") == raw


def test_score_is_color_coded_and_shows_signal_confidence(client):
    job_id = add_job(rank=82)
    store.update_job_signals(job_id, 82, '{"fit": 80, "confidence": 0.9}', "signals-key", 0.9)
    score_box = soup(client.get("/jobs")).select_one(".vacancy-score")
    assert "high" in score_box["class"]
    assert "82" in score_box.get_text()
    assert "90% zeker" in score_box.get_text()


def test_signal_tags_are_compact_and_above_the_filters(client, monkeypatch):
    add_job()
    monkeypatch.setattr(
        main.feedback,
        "load",
        lambda: {
            "counts": {"up": 2, "down": 1},
            "liked_keywords": [{"keyword": "buiten"}],
            "disliked_keywords": [{"keyword": "sales"}],
        },
    )
    response = client.get("/jobs")
    html = soup(response)
    card = html.select_one(".jobs-signals")
    assert card is not None
    assert "2 interessant" in card.get_text()
    assert "1 minder passend" in card.get_text()
    assert card.select_one(".chip-ok").get_text() == "buiten"
    assert card.select_one(".chip-warn").get_text() == "sales"
    assert response.text.index("jobs-signals") < response.text.index("jobs-filters")
    assert not html.select(".jobs-insights .feedback-card")


def test_search_tabs_chips_and_sort_preserve_state(client):
    add_job()
    html = soup(client.get("/jobs?q=Groen&within=0&source=test&sort=newest&per_page=10"))
    for tab in html.select(".status-tabs a"):
        params = parse_qs(urlsplit(tab["href"]).query)
        assert params["q"] == ["Groen"]
        assert params["within"] == ["0"]
        assert params["source"] == ["test"]
        assert params["sort"] == ["newest"]
        assert params["per_page"] == ["10"]
    chips = html.select(".jobs-active-filters .chip")
    assert len(chips) == 3
    assert "q=Groen" not in chips[0]["href"]
    assert "within=1" in chips[2]["href"]
    assert not html.select_one('input[type="checkbox"][name="within"]').has_attr("checked")


def test_sort_and_travel_filter_apply_before_pagination(client):
    add_job("Older best", date="2026-09-01", rank=99)
    add_job("Newer", date="2026-10-01", rank=40)
    add_job("Far newest", location="far", date="2026-10-18", rank=100)
    html = soup(client.get("/jobs?sort=newest"))
    assert [el.get_text() for el in html.select(".vacancy-main h3")] == ["Newer", "Older best"]
    html = soup(client.get("/jobs?sort=rank&within=0"))
    assert [el.get_text() for el in html.select(".vacancy-main h3")] == [
        "Far newest",
        "Older best",
        "Newer",
    ]
    assert "3 resultaten" in html.get_text()


def test_no_hidden_500_row_ceiling(client):
    with store.connect() as con:
        con.executemany(
            "INSERT INTO jobs(url, title, location, rank_score, first_seen) VALUES (?,?,?,?,?)",
            [
                (f"https://example.test/{i}", f"Job {i}", "unknown", i, "2026-10-01")
                for i in range(520)
            ],
        )
    html = soup(client.get("/jobs?within=0&page=52&per_page=10"))
    assert "520 resultaten" in html.get_text()
    assert len(html.select(".vacancy-row")) == 10
    assert "Pagina 52 van 52" in html.select_one(".jobs-pager").get_text()


def test_newest_normalizes_feed_dates_and_falls_back_to_discovery(client):
    add_job("RFC latest", date="Sun, 18 Oct 2026 12:00:00 +0200")
    add_job("ISO older", date="2026-10-17T12:00:00Z")
    undated = add_job("Undated", date="")
    invalid = add_job("Invalid date", date="not a date")
    with store.connect() as con:
        con.execute("UPDATE jobs SET first_seen=? WHERE id=?", ("2026-10-19T10:00:00Z", undated))
        con.execute("UPDATE jobs SET first_seen=? WHERE id=?", ("2026-10-16T10:00:00Z", invalid))
    assert [row["title"] for row in store.list_jobs(2, sort="newest")] == ["Undated", "RFC latest"]
    html = soup(client.get("/jobs?sort=newest"))
    assert [el.get_text() for el in html.select(".vacancy-main h3")] == [
        "Undated",
        "RFC latest",
        "ISO older",
        "Invalid date",
    ]


def test_checkbox_duplicate_values_follow_checked_state(client):
    add_job("Far", location="far")
    html = soup(client.get("/jobs?within=0&within=1"))
    assert html.select_one('input[type="checkbox"][name="within"]').has_attr("checked")
    assert not html.select(".vacancy-row")
    assert len(soup(client.get("/jobs?within=0")).select(".vacancy-row")) == 1


@pytest.mark.parametrize(
    "action,payload", [("status", {"status": "saved"}), ("vote", {"vote": "up"})]
)
def test_actions_keep_filters_sort_page_and_anchor(client, action, payload):
    job_id = add_job()
    target = f"/jobs?q=groen&within=0&sort=newest&status=new&page=2#job-{job_id}"
    response = client.post(
        f"/jobs/{job_id}/{action}", data={**payload, "next": target}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == target
    job = store.get_job(job_id)
    assert job["status"] == "saved" if action == "status" else job["vote"] == 1


@pytest.mark.parametrize(
    "target",
    [
        "//example.com",
        "https://example.com",
        "/profile",
        "/\\example.com",
        "/jobs\nInjected",
        "http://[broken",
        " /jobs ",
    ],
)
def test_status_rejects_external_or_unrelated_return_urls(client, target):
    job_id = add_job()
    response = client.post(
        f"/jobs/{job_id}/status", data={"status": "saved", "next": target}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/jobs"


def test_status_from_detail_stays_on_detail_and_unknown_job_is_404(client):
    job_id = add_job()
    response = client.post(
        f"/jobs/{job_id}/status",
        data={"status": "saved", "next": f"/jobs/{job_id}"},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"/jobs/{job_id}"
    assert client.post("/jobs/999/status", data={"status": "saved"}).status_code == 404


def test_voted_button_can_clear_vote(client):
    job_id = add_job()
    store.set_job_vote(job_id, 1)
    html = soup(client.get("/jobs"))
    assert html.select_one('button[aria-pressed="true"]')["value"] == "clear"
    response = client.post(f"/jobs/{job_id}/vote", data={"vote": "clear"}, follow_redirects=False)
    assert response.status_code == 303
    assert store.get_job(job_id)["vote"] == 0


def test_running_does_not_reload_the_page_and_errors_offer_recovery(client):
    store.meta_set("match_status", "running")
    html = soup(client.get("/jobs"))
    assert html.select_one('#jobs-progress[data-match-running="true"]')
    assert not html.select('meta[http-equiv="refresh"]')
    assert html.select_one(".jobs-matching button").has_attr("disabled")
    store.meta_set("match_status", "error: provider unavailable")
    html = soup(client.get("/jobs"))
    assert "Probeer het opnieuw" in html.select_one("#jobs-progress").get_text()
    assert not html.select_one(".jobs-matching button").has_attr("disabled")


def test_match_action_preserves_view_and_starts_only_once(client, monkeypatch):
    monkeypatch.setattr(main, "manager", SimpleNamespace(client=object()))
    target = "/jobs?q=groen&within=0&sort=newest"
    for _ in range(2):
        response = client.post("/jobs/match", data={"next": target}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == target
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
        rows = con.execute(
            "SELECT COUNT(*) FROM tasks WHERE kind='match' AND owner=?", (owner,)
        ).fetchone()[0]
    assert rows == 1


def test_match_history_records_completion_time_without_provider_calls(client, monkeypatch):
    monkeypatch.setattr(
        main.matching, "match_jobs", lambda manager: {"matched": 0, "signals": 0, "cost_usd": 0}
    )
    main._run_match()
    summary = json.loads(store.meta_get("match_summary"))
    assert summary["finished_at"].endswith("+00:00")
    assert store.meta_get("match_status") == "done"


def test_drawer_links_are_real_detail_urls_with_list_context(client):
    job_id = add_job()
    html = soup(client.get("/jobs?q=Groen&within=0&sort=newest&per_page=10"))
    assert html.select_one('dialog#job-drawer[aria-labelledby="job-drawer-title"]')
    assert html.select_one('script[src="/static/job-drawer.js"]')
    links = html.select(f"#job-{job_id} [data-job-link]")
    assert len(links) == 2
    for link in links:
        url = urlsplit(link["href"])
        assert url.path == f"/jobs/{job_id}"
        target = urlsplit(parse_qs(url.query)["return_to"][0])
        assert target.path == "/jobs"
        assert target.fragment == f"job-{job_id}"
        assert parse_qs(target.query)["q"] == ["Groen"]
        assert parse_qs(target.query)["sort"] == ["newest"]


def test_shared_detail_supports_drawer_and_full_page_return_context(client):
    job_id = add_job()
    target = f"/jobs?q=Groen&within=0&page=2#job-{job_id}"
    html = soup(client.get(f"/jobs/{job_id}", params={"return_to": target}))
    detail = html.select_one("[data-job-detail]")
    assert detail["data-job-id"] == str(job_id)
    assert detail["data-job-status"] == "new"
    assert detail["data-job-score"] == "61.0"
    assert html.select_one(".detail-back a")["href"] == target
    for field in detail.select('input[name="next"]'):
        url = urlsplit(field["value"])
        assert url.path == f"/jobs/{job_id}"
        assert parse_qs(url.query)["return_to"] == [target]
    assert len(detail.select("h1")) == 1
    assert len(html.select("main")) == 1


@pytest.mark.parametrize(
    "target", ["https://example.test", "//example.test", "/profile", "/jobs/123"]
)
def test_detail_back_link_rejects_unrelated_return_targets(client, target):
    job_id = add_job()
    html = soup(client.get(f"/jobs/{job_id}", params={"return_to": target}))
    assert html.select_one(".detail-back a")["href"] == "/jobs"


def test_document_post_retains_context_without_starting_a_second_generation(client, monkeypatch):
    job_id = add_job()
    monkeypatch.setattr(main, "manager", object())
    store.meta_set(f"doc_status:{job_id}", '{"state":"running","kind":"resume"}')
    target = f"/jobs/{job_id}?return_to=%2Fjobs%3Fq%3DGroen"
    response = client.post(
        f"/jobs/{job_id}/documents", data={"kind": "resume", "next": target}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == target + "#documenten"
    html = soup(client.get(response.headers["location"]))
    assert html.select_one('[data-job-detail][data-document-state="running"]')
    assert html.select_one("#detail-resume").has_attr("disabled")


def test_document_requests_from_multiple_views_start_one_generation(client, monkeypatch):
    job_id = add_job()
    starts = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.args = kwargs["args"]

        def start(self):
            starts.append(self.args)

    original_set = store.meta_set

    def slow_status_set(key, value):
        if key == f"doc_status:{job_id}":
            time.sleep(0.05)
        original_set(key, value)

    monkeypatch.setattr(main, "manager", object())
    monkeypatch.setattr(main, "threading", SimpleNamespace(Thread=FakeThread))
    monkeypatch.setattr(store, "meta_set", slow_status_set)
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(
            executor.map(
                lambda _: client.post(
                    f"/jobs/{job_id}/documents", data={"kind": "resume"}, follow_redirects=False
                ),
                range(4),
            )
        )
    assert all(response.status_code == 303 for response in responses)
    assert starts == [(job_id, "resume")]


def test_drawer_focus_targets_are_stable_across_document_refreshes(client):
    job_id = add_job()
    store.add_document(job_id, "resume", 1, "fixture/resume-v1.docx", "fixture/resume-v1.pdf", "{}")
    detail = soup(client.get(f"/jobs/{job_id}")).select_one("[data-job-detail]")
    assert all(
        element.get("id")
        for element in detail.select('a[href]:not(.detail-back a), button, select, [tabindex="0"]')
    )
    assert detail.select_one("#detail-text-title")
    assert detail.select_one("#detail-assessment-title")
    assert detail.select_one("#detail-documents-title")
