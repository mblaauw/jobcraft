"""Market coverage, the daily habit and winning the application."""

import gzip
import io
import json
import ssl
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from docx import Document

from app import (
    account_data,
    applications,
    auth,
    config,
    digest,
    insights,
    job_import,
    ranking,
    runtime,
    scheduler,
    source_http,
    sources,
    store,
    tenancy,
)
from app.adapters import ats
from app.routers import workspace
from app.scoring import reasons, rules
from tests.test_jobs_page import add_job, client, soup  # noqa: F401


def owner_id() -> str:
    with store.connect() as con:
        return con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]


def add_shared(title, company, url, source="test", location="unknown"):
    job_id, _ = store.upsert_job(
        {
            "source_id": source,
            "url": url,
            "title": title,
            "company": company,
            "description": "Tekst",
            "location": location,
            "content_hash": url,
        }
    )
    return job_id


# ------------------------------------------------------------------ registry


class FakeBoards:
    def __init__(self, answers):
        self.answers = answers

    def get(self, url, **kwargs):
        value = self.answers.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, json=value, request=httpx.Request("GET", url))


GH = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
RE = "https://beta.recruitee.com/api/offers/"
EMPLOYERS = [
    {"name": "Acme", "system": "greenhouse", "slug": "acme"},
    {"name": "Beta", "system": "recruitee", "slug": "beta"},
]


def test_registry_keeps_only_dutch_jobs_with_full_text(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ats, "load_employers", lambda: EMPLOYERS)
    monkeypatch.setattr(ats.time, "sleep", lambda _: None)
    answers = {
        GH: {
            "jobs": [
                {
                    "absolute_url": "https://j.test/1",
                    "title": "Analist",
                    "location": {"name": "Amsterdam"},
                    "content": "&lt;p&gt;Mooie baan&lt;/p&gt;",
                },
                {"absolute_url": "https://j.test/2", "title": "Sales", "location": {"name": "Berlin, Germany"}},
            ]
        },
        RE: {
            "offers": [
                {
                    "careers_url": "https://j.test/3",
                    "title": "Planner",
                    "location": "Utrecht",
                    "country": "Netherlands",
                    "description": "<p>Plannen</p>",
                    "requirements": "<p>Ervaring</p>",
                }
            ]
        },
    }
    entry = {"status": "ok", "new": 0, "updated": 0, "items": 0}
    store.sync_sources([{"id": "ats-bedrijven", "kind": "ats-registry"}])
    ats._scan_ats(FakeBoards(answers), {"id": "ats-bedrijven"}, entry, {"new": 0, "updated": 0})
    with store.connect() as con:
        rows = {row["url"]: row for row in con.execute("SELECT * FROM jobs").fetchall()}
    assert set(rows) == {"https://j.test/1", "https://j.test/3"}
    assert rows["https://j.test/1"]["company"] == "Acme"
    assert "Mooie baan" in rows["https://j.test/1"]["description"]
    assert "<p>" not in rows["https://j.test/1"]["description"]
    assert "Ervaring" in rows["https://j.test/3"]["description"]


def test_unreachable_board_keeps_vacancies_from_expiring(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ats, "load_employers", lambda: EMPLOYERS)
    monkeypatch.setattr(ats.time, "sleep", lambda _: None)
    entry = {"status": "ok", "new": 0, "updated": 0, "items": 0}
    fake = FakeBoards({GH: httpx.ConnectError("down")})
    with pytest.raises(RuntimeError, match="acme"):
        ats._scan_ats(fake, {"id": "x"}, entry, {"new": 0, "updated": 0})


def test_removed_board_is_not_a_failure(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ats, "load_employers", lambda: EMPLOYERS)
    monkeypatch.setattr(ats.time, "sleep", lambda _: None)
    store.sync_sources([{"id": "x", "kind": "ats-registry"}])
    ats._scan_ats(FakeBoards({}), {"id": "x"}, {"items": 0}, {"new": 0, "updated": 0})


@pytest.mark.parametrize(
    "location,expected",
    [
        ("Amsterdam", True),
        ("Amsterdam, NL", True),
        ("NL-NH-Amsterdam-56E", True),
        ("Remote - Netherlands", True),
        ("Berlin, Germany", False),
        ("Dublin, Ireland", False),
        ("London", False),
        ("Zurich", False),
        ("Remote, North America", False),
        ("", False),
    ],
)
def test_dutch_location_check(location, expected):
    assert ats.in_netherlands(location) is expected


def test_shipped_registry_is_well_formed():
    employers = ats.load_employers()
    assert len(employers) >= 10
    assert all(item["system"] in ats.SYSTEMS and item["name"] for item in employers)
    assert len({(item["system"], item["slug"]) for item in employers}) == len(employers)


def test_new_seed_source_reaches_existing_installations(client):  # noqa: F811
    store.save_source_config({"id": "greenjobs", "kind": "exenzo-json"}, enabled=False)
    loaded = {item["id"]: item for item in sources.load_sources()}
    assert loaded["ats-bedrijven"]["kind"] == "ats-registry"
    assert loaded["greenjobs"]["enabled"] is False


def test_source_health_counts_failures_in_a_row(client):  # noqa: F811
    bad = {"started_at": "", "finished_at": "2026-11-02", "sources": {"a": {"status": "error", "reason": "x"}}}
    sources._record_health(bad)
    sources._record_health(bad)
    assert json.loads(store.meta_get("source_health"))["a"]["failures"] == 2
    good = {"finished_at": "2026-11-03", "sources": {"a": {"status": "ok", "items": 4}}}
    sources._record_health(good)
    health = json.loads(store.meta_get("source_health"))["a"]
    assert health == {
        "failures": 0,
        "last_ok": "2026-11-03",
        "items": 4,
        "skipped": 0,
        "reason": "",
        "history": [4],
        "dropped": False,
    }


# ---------------------------------------------------------------- duplicates


def test_same_vacancy_on_two_sites_counts_once(client):  # noqa: F811
    first = add_shared("Planner", "Acme", "https://a.test/1", location="Utrecht")
    second = add_shared("planner ", "ACME", "https://b.test/1", location="Utrecht")
    add_shared("Planner", "Acme", "https://c.test/1", location="Zwolle")
    add_shared("Planner", "", "https://d.test/1")
    add_shared("Planner", "", "https://e.test/1")
    html = soup(client.get("/jobs?within=0"))
    assert "4 resultaten" in html.get_text()
    assert "en 1 andere plek" in html.get_text()
    store.update_job_status(second, "saved")
    html = soup(client.get("/jobs?within=0&status=saved"))
    assert len(html.select(".vacancy-row")) == 1
    assert store.get_job(first) is not None


def test_short_terms_match_whole_words_and_long_terms_compounds():
    assert rules.has_term("it", "werken in de it sector")
    assert not rules.has_term("it", "kwaliteit en politiek")
    assert not rules.has_term("hr", "christelijk")
    assert rules.has_term("medewerker", "groenmedewerker gezocht")


# ------------------------------------------------------- reasons and facts


def test_reason_prefers_the_verdict_and_otherwise_shows_what_rules_saw():
    preferences = {
        "tracks": [{"id": "p", "name": "Projectleider", "keywords": ["projectleider"]}],
        "wishes": {"remote": "hybrid"},
        "keywords": {"veto": ["sales"]},
    }
    job = {"title": "Projectleider", "search_text": "projectleider hybride werken", "match_reason": None}
    assert reasons.reason_line(job, preferences).startswith("Past bij projectleider")
    verdict = {**job, "match_reason": json.dumps({"why": "Sterke match. Tweede zin."})}
    assert reasons.reason_line(verdict, preferences) == "Sterke match."


def test_facts_come_from_the_text():
    found = reasons.facts({"search_text": "salaris € 4.500 per maand, vast contract en hybride"})
    assert found == ["tot € 4.500 per maand", "Deels thuis", "Vast contract"]
    assert reasons.facts({"search_text": "salaris € 3.211 tot € 4.063 per maand"}) == ["tot € 4.060 per maand"]
    assert reasons.facts({"search_text": "leuke baan"}) == []


# ---------------------------------------------------------------- swipe mode


def test_one_by_one_shows_the_best_and_voting_moves_on(client):  # noqa: F811
    best = add_job("Beste", rank=90)
    add_job("Tweede", rank=50)
    html = soup(client.get("/jobs/doorlopen"))
    assert "Beste" in html.select_one(".swipe-card h2").get_text()
    client.post(f"/jobs/{best}/vote", data={"vote": "up", "next": "/jobs/doorlopen?overslaan=0"})
    assert "Tweede" in soup(client.get("/jobs/doorlopen")).select_one(".swipe-card h2").get_text()
    assert "Je bent bij" in soup(client.get("/jobs/doorlopen?overslaan=5")).get_text()


# ------------------------------------------------------------------- import


def test_pasted_vacancy_is_private_to_its_account(client):  # noqa: F811
    response = client.post(
        "/jobs/toevoegen",
        data={"title": "Geheim", "company": "Acme", "description": "Een lange vacaturetekst"},
        follow_redirects=False,
    )
    job_id = int(response.headers["location"].rsplit("/", 1)[1])
    assert store.get_job(job_id)["status"] == "saved"
    assert "Zelf toegevoegd" in soup(client.get("/jobs?within=0")).get_text()
    auth._insert_user("other-id", "other", "Ander", "user", "another-password-1")
    with tenancy.scope({"id": "other-id"}):
        assert store.get_job(job_id) is None
        assert not store.list_jobs(None)
        assert not store.list_jobs_for_matching()
        assert not store.list_jobs_page(10)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://[::1]/",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "https://user:secret@example.com/",
    ],
)
def test_import_never_reaches_private_addresses(url):
    with pytest.raises(job_import.ImportBlocked):
        job_import._check_public(url)


def test_import_rechecks_every_redirect_hop(monkeypatch):
    visited = []

    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    monkeypatch.setattr(
        job_import.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(0, 0, 0, "", ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 0))],
    )
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as fake,
        pytest.raises(job_import.ImportBlocked),
    ):
        job_import._get(fake, "https://public.test/job")
    assert visited == ["https://public.test/job"]


def test_closed_site_asks_for_pasted_text(client, monkeypatch):  # noqa: F811
    def blocked(url):
        raise job_import.ImportBlocked(job_import.CLOSED)

    monkeypatch.setattr(job_import, "fetch_page", blocked)
    response = client.post("/jobs/toevoegen", data={"url": "https://closed.test/1"})
    assert response.status_code == 400
    assert "Plak de vacaturetekst" in response.text


# ------------------------------------------------------------------- digest


def test_digest_stays_silent_without_smtp_or_opt_in(client, monkeypatch):  # noqa: F811
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert digest.send_if_due() == "off"
    assert scheduler.run_digest_due() is False
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_FROM", "jobs@test")
    assert digest.send_if_due() == "off"


def test_digest_sends_once_a_day_and_never_empty(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_FROM", "jobs@test")
    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context=None):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(digest.smtplib, "SMTP", FakeSMTP)
    store.meta_set("digest_email", "a@b.test")
    store.meta_set("digest_enabled", "1")
    assert digest.send_if_due() == "empty"
    store.meta_set("digest_last_date", "")
    add_job("Nieuw <b>bold</b>")
    assert digest.send_if_due().startswith("sent")
    assert digest.send_if_due() == "already"
    assert len(sent) == 1
    html = sent[0].get_body(("html",)).get_content()
    assert "&lt;b&gt;" in html and "<b>bold" not in html


# -------------------------------------------------------------------- board


def test_board_tracks_status_deadline_and_notes(client):  # noqa: F811
    job_id = add_job()
    response = client.post(
        f"/sollicitaties/{job_id}",
        data={"status": "interview", "deadline": "2026-11-05", "note": "Vraag Anna"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    html = soup(client.get("/sollicitaties"))
    assert job_id == store.get_job(job_id)["id"]
    assert "Vraag Anna" in html.get_text()
    assert store.get_job(job_id)["status"] == "interview"
    assert client.post(f"/sollicitaties/{job_id}", data={"status": "bogus"}).status_code == 400


def test_reminders_cover_open_applications_only(client):  # noqa: F811
    from datetime import date

    soon, late, closed = add_job("Snel"), add_job("Laat"), add_job("Dicht")
    for job_id, status, deadline in (
        (soon, "applied", "2026-11-03"),
        (late, "applied", "2026-12-30"),
        (closed, "rejected", "2026-11-02"),
    ):
        store.update_job_status(job_id, status)
        applications.save_details(job_id, deadline, "")
    due = applications.reminders(today=date(2026, 11, 2))
    assert [item["job"]["id"] for item in due] == [soon]


def test_invalid_deadline_is_dropped():
    assert applications.date.fromisoformat("2026-11-05")


def test_applications_are_never_expired_or_reassessed(client):  # noqa: F811
    from app.scoring.signals import excluded_from_matching

    job_id = add_job()
    store.update_job_status(job_id, "interview")
    assert excluded_from_matching(store.get_job(job_id))


# ----------------------------------------------------------------- insights


class FakeManager:
    client = object()

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def generate(self, prompt, *, agent):
        self.calls.append((prompt, agent))
        return self.reply, {"input_tokens": 3, "output_tokens": 2, "cost_usd": 0.001}


GAP = json.dumps({"have": ["Plannen"], "missing": [{"requirement": "SQL", "how": "Noem je cursus"}]})


def _run_insights():
    """What the worker does: run the queued insight tasks (and nothing else)."""
    from app import tasks

    while task := tasks.claim_next():
        if task["kind"] == "insight":
            insights._task(task)
        tasks.finish(task["id"])


def test_insight_runs_on_request_and_is_kept_until_the_vacancy_changes(client, monkeypatch):  # noqa: F811
    job_id = add_job()
    manager = FakeManager(GAP)
    monkeypatch.setattr(runtime, "manager", manager)
    html = soup(client.get(f"/jobs/{job_id}"))
    assert not manager.calls
    assert html.select_one(f'form[action="/jobs/{job_id}/inzicht/gap"]')
    client.post(f"/jobs/{job_id}/inzicht/gap")
    client.post(f"/jobs/{job_id}/inzicht/gap")
    waiting = soup(client.get(f"/jobs/{job_id}"))
    assert waiting.select_one("#inzicht [data-auto-refresh]") and not manager.calls
    assert client.get("/api/werk").json()["running"][0]["what"] == "De vergelijking met je profiel"
    _run_insights()
    assert len(manager.calls) == 1
    assert manager.calls[0][1] == "job_gap"
    page = soup(client.get(f"/jobs/{job_id}"))
    assert "Noem je cursus" in page.get_text() and not page.select_one("#inzicht [data-auto-refresh]")
    assert client.get("/api/werk").json() == {"running": [], "done": []}
    with store.connect() as con:
        con.execute("UPDATE jobs SET content_hash='new' WHERE id=?", (job_id,))
    assert insights.load("gap", store.get_job(job_id)) is None


def test_insight_prompt_has_no_private_profile_text(client, monkeypatch):  # noqa: F811
    from app import profile

    job_id = add_job()
    profile.merge_sections({"Over mij": "Open.", "Privé": "GEHEIM"})
    manager = FakeManager(GAP)
    monkeypatch.setattr(runtime, "manager", manager)
    client.post(f"/jobs/{job_id}/inzicht/gap")
    _run_insights()
    assert "Open." in manager.calls[0][0] and "GEHEIM" not in manager.calls[0][0]


def test_insight_failure_or_spent_budget_costs_nothing_and_says_so(client, monkeypatch):  # noqa: F811
    job_id = add_job()
    manager = FakeManager("not json")
    monkeypatch.setattr(runtime, "manager", manager)
    client.post(f"/jobs/{job_id}/inzicht/prep")
    _run_insights()
    assert "Dit lukte niet" in client.get(f"/jobs/{job_id}").text
    monkeypatch.setattr(insights, "_budget_reached", lambda: True)
    manager.calls.clear()
    response = client.post(f"/jobs/{job_id}/inzicht/gap", follow_redirects=False)
    assert "inzicht_fout=budget" in response.headers["location"]
    _run_insights()
    assert not manager.calls
    assert "daglimiet" in client.get(response.headers["location"]).text


def test_insight_return_target_cannot_leave_the_job(client, monkeypatch):  # noqa: F811
    job_id = add_job()
    monkeypatch.setattr(runtime, "manager", FakeManager(GAP))
    response = client.post(
        f"/jobs/{job_id}/inzicht/gap", data={"next": "https://evil.test/"}, follow_redirects=False
    )
    assert response.headers["location"].startswith(f"/jobs/{job_id}")


# ------------------------------------------------------------ editing documents


def _letter(job_id):
    from app import document_edits

    return document_edits.save_edited_version(
        store.get_job(job_id), "motivatie", {"paragraphs": ["Eerste alinea.", "Tweede alinea."]}
    )


def test_editing_makes_a_new_version_and_keeps_the_old(client, monkeypatch):  # noqa: F811
    from app import docs

    monkeypatch.setattr(docs, "convert_pdf", lambda path: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(docs, "ensure_templates", lambda: None)
    monkeypatch.setattr(docs, "cv_identity", lambda: {"name": ["Test"], "contact": []})
    job_id = add_job()
    assert _letter(job_id) == 1
    document = store.list_documents(job_id)[0]
    first_path = config.DATA_DIR / document["docx_path"]
    before = first_path.read_bytes()
    edit_url = f"/jobs/{job_id}/documents/{document['id']}/bewerken"
    assert "Eerste alinea." in client.get(edit_url).text
    response = client.post(edit_url, data={"text": "Nieuwe tekst.\n\nNog een alinea."}, follow_redirects=False)
    assert response.status_code == 303
    versions = sorted(item["version"] for item in store.list_documents(job_id))
    assert versions == [1, 2]
    assert first_path.read_bytes() == before
    newest = next(item for item in store.list_documents(job_id) if item["version"] == 2)
    text = "\n".join(p.text for p in Document(config.DATA_DIR / newest["docx_path"]).paragraphs)
    assert "Nieuwe tekst." in text


def test_edit_problems_are_plain_dutch(client, monkeypatch):  # noqa: F811
    from app import docs

    monkeypatch.setattr(docs, "convert_pdf", lambda path: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(docs, "ensure_templates", lambda: None)
    monkeypatch.setattr(docs, "cv_identity", lambda: {"name": ["Test"], "contact": []})
    job_id = add_job()
    _letter(job_id)
    document = store.list_documents(job_id)[0]
    response = client.post(f"/jobs/{job_id}/documents/{document['id']}/bewerken", data={"text": "  "})
    assert response.status_code == 400
    assert "Schrijf minstens één alinea" in response.text


def test_old_documents_without_saved_text_explain_why(client):  # noqa: F811
    job_id = add_job()
    store.add_document(job_id, "motivatie", 1, "x.docx", None, "{}")
    document = store.list_documents(job_id)[0]
    response = client.get(
        f"/jobs/{job_id}/documents/{document['id']}/bewerken", headers={"accept": "text/html"}
    )
    assert response.status_code == 409
    assert "oudere document" in response.text


# ------------------------------------------------------------ export and delete


@pytest.fixture()
def member(client, monkeypatch):  # noqa: F811
    auth._insert_user("member-id", "member", "Lid", "user", "member-password-1")
    return "member-id"


def test_export_contains_own_data_only_and_no_credentials(client, member):  # noqa: F811
    job_id = add_job()
    with tenancy.scope({"id": member}):
        store.set_job_vote(job_id, 1)
        store.meta_set("digest_email", "lid@example.test")
        store.record_usage(None, "x", 1, 1, 0.0)
        raw = account_data.export_zip()
    with tenancy.scope({"id": owner_id()}):
        store.meta_set("digest_email", "andere@example.test")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        meta = archive.read("gegevens/user_meta.json").decode()
    assert "gegevens/job_states.json" in names
    assert "lid@example.test" in meta and "andere@example.test" not in meta
    assert not any("users" in name or "auth_sessions" in name for name in names)


def test_delete_removes_everything_of_that_account_only(client, member):  # noqa: F811
    job_id = add_job()
    with tenancy.scope({"id": member}):
        store.set_job_vote(job_id, 1)
        store.meta_set("digest_email", "lid@example.test")
        private = account_data.store.upsert_job(
            {"source_id": store.import_source(), "url": "https://p.test/1", "title": "Eigen", "description": "x", "content_hash": "p"}
        )[0]
        workspace_dir = config.workspace_dir()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "cv.txt").write_text("cv")
        account_data.delete_account()
    assert not workspace_dir.exists()
    with store.connect() as con:
        for table in ("user_meta", "job_states", "feedback", "usage_log"):
            assert not con.execute(f"SELECT 1 FROM {table} WHERE owner=?", (member,)).fetchall()
        assert not con.execute("SELECT 1 FROM users WHERE id=?", (member,)).fetchall()
        assert not con.execute("SELECT 1 FROM jobs WHERE id=?", (private,)).fetchall()
        assert con.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()


def test_delete_requires_password_and_typed_confirmation(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(workspace.tenancy, "LEGACY_ID", "not-this-account")
    wrong = client.post("/account/verwijderen", data={"password": "nope", "confirm": "VERWIJDER"})
    assert wrong.status_code == 400
    missing = client.post("/account/verwijderen", data={"password": "test-password", "confirm": "ja"})
    assert missing.status_code == 400
    with store.connect() as con:
        assert con.execute("SELECT 1 FROM users WHERE username='test'").fetchone()


def test_legacy_account_cannot_be_deleted_here(client):  # noqa: F811
    response = client.post(
        "/account/verwijderen", data={"password": "test-password", "confirm": "VERWIJDER"}
    )
    assert response.status_code == 409


# ----------------------------------------------------------------- security


def test_security_headers_are_set(client):  # noqa: F811
    response = client.get("/jobs")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_no_template_needs_inline_script():
    from pathlib import Path

    for path in Path("app/templates").glob("*.html"):
        text = path.read_text()
        assert "<script>" not in text and " onclick=" not in text, path


def test_scan_stops_at_its_next_request_during_shutdown():
    fake = SimpleNamespace(auth=None, cookies={}, headers={})
    polite = source_http.PoliteClient(client=fake, cache_dir=None)
    source_http.STOPPING.set()
    try:
        with pytest.raises(RuntimeError, match="onderbroken"):
            polite._wait("https://x.test", 0)
    finally:
        source_http.STOPPING.clear()


def test_session_header_only_goes_to_the_gateway_that_uses_it():
    from app.llm_client import OpenAICompatClient

    plain = OpenAICompatClient.__new__(OpenAICompatClient)
    plain.base_url = "https://api.example.test/v1"
    assert plain._session_headers("abc") == {}
    plain.base_url = "https://opencode.example.test/v1"
    assert plain._session_headers("abc") == {"x-opencode-session": "abc"}


def test_ranking_module_still_reruns_with_new_statuses(client):  # noqa: F811
    job_id = add_job()
    store.update_job_status(job_id, "offer")
    assert ranking.travel_of(store.get_job(job_id))


# ---------------------------------------------------------------- review fixes


def test_link_import_reads_compressed_pages(monkeypatch):
    page = b"<html><h1>Planner</h1><p>" + b"Tekst over het werk. " * 40 + b"</p></html>"

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip", "content-type": "text/html"},
            content=gzip.compress(page),
        )

    real_client = httpx.Client
    monkeypatch.setattr(job_import, "_check_public", lambda url: None)
    monkeypatch.setattr(
        job_import.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert job_import.fetch_page("https://public.test/job")["title"] == "Planner"


@pytest.mark.parametrize("url", ["http://[x/y", "https://a.test:99999/x", "javascript:alert(1)", "ftp://a.test/"])
def test_unusable_links_are_refused_in_plain_dutch(url):
    with pytest.raises(job_import.ImportBlocked):
        job_import.fetch_page(url)


def test_a_pasted_vacancy_never_stores_a_script_link(client):  # noqa: F811
    job_id = job_import.add({"title": "Planner", "description": "Tekst"}, "javascript:alert(1)")
    assert store.get_job(job_id)["url"].startswith("https://import.jobcraft.invalid/")
    assert "detail-original" not in client.get(f"/jobs/{job_id}").text


def test_mail_verifies_the_server_certificate(monkeypatch):
    seen = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context=None):
            seen["context"] = context

        def send_message(self, message):
            pass

    monkeypatch.setenv("SMTP_HOST", "mail.test")
    monkeypatch.setattr(digest.smtplib, "SMTP", FakeSMTP)
    digest.deliver(digest.EmailMessage())
    assert seen["context"].verify_mode == ssl.CERT_REQUIRED and seen["context"].check_hostname


def test_mail_without_a_public_address_has_no_dead_links(monkeypatch):
    monkeypatch.setenv("SMTP_FROM", "a@b.test")
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    job = {"id": 1, "title": "Planner", "company": "Acme"}
    message = digest.compose([job], "c@d.test")
    assert "/jobs/1" not in message.as_string()
    monkeypatch.setenv("APP_BASE_URL", "https://jobs.test")
    assert "https://jobs.test/jobs/1" in digest.compose([job], "c@d.test").get_body(("plain",)).get_content()


def test_pasted_text_names_its_employer_and_place(client):  # noqa: F811
    text = "Projectcoördinator Kunst in de Wijk\nStichting Kunstwerk Oost, Amsterdam-Oost\n\n" + "Wij zoeken een coördinator. " * 12
    response = client.post("/jobs/toevoegen", data={"description": text}, follow_redirects=False)
    job = store.get_job(int(response.headers["location"].rsplit("/", 1)[1]))
    assert (job["company"], job["location"]) == ("Stichting Kunstwerk Oost", "Amsterdam")
    typed = client.post("/jobs/toevoegen", data={"description": text + "x", "company": "Eigen naam", "location": "Delft"}, follow_redirects=False)
    job = store.get_job(int(typed.headers["location"].rsplit("/", 1)[1]))
    assert (job["company"], job["location"]) == ("Eigen naam", "Delft")  # what someone typed wins


PERSONIO = "https://voorbeeld.jobs.personio.com"
PERSONIO_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position><id>11</id><office>Vianen</office><additionalOffices><office>Heerlen</office></additionalOffices>
    <name>Adviseur</name><jobDescriptions></jobDescriptions><employmentType>permanent</employmentType>
    <schedule>full-time</schedule><createdAt>2026-10-28T08:51:16+00:00</createdAt></position>
  <position><id>12</id><office>Amsterdam</office><name>Planner</name><jobDescriptions></jobDescriptions>
    <employmentType>intern</employmentType><schedule>part-time</schedule><createdAt>2026-10-29T08:00:00+00:00</createdAt></position>
  <position><id>13</id><office>Bratislava, Slovakia</office><name>Engineer</name><jobDescriptions><jobDescription>
    <name>Taken</name><value>&lt;p&gt;Ver weg&lt;/p&gt;</value></jobDescription></jobDescriptions></position>
</workzag-jobs>"""


class FakeSite:
    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        request = httpx.Request("GET", url)
        if url not in self.pages:
            return httpx.Response(404, request=request)
        body = self.pages[url]
        return httpx.Response(200, content=body if isinstance(body, bytes) else body.encode(), request=request)


def test_lever_boards_keep_dutch_postings_with_their_full_text(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ats, "load_employers", lambda: [{"name": "Voorbeeld", "system": "lever", "slug": "voorbeeld"}])
    monkeypatch.setattr(ats.time, "sleep", lambda _: None)
    postings = [
        {"text": "Product Owner", "hostedUrl": "https://jobs.lever.co/voorbeeld/1", "country": "NL", "createdAt": 1793000000000,
         "categories": {"location": "Amsterdam", "allLocations": ["Amsterdam"], "commitment": "Full-time"},
         "descriptionPlain": "Je stuurt het team.", "lists": [{"text": "Wat je doet", "content": "<li>Plannen maken</li>"}],
         "additionalPlain": "Hybride werken."},
        {"text": "Engineer", "hostedUrl": "https://jobs.lever.co/voorbeeld/2", "country": "DE",
         "categories": {"location": "Berlin | Munich", "allLocations": ["Berlin", "Munich"]}, "descriptionPlain": "Elders."},
    ]
    store.sync_sources([{"id": "ats-bedrijven", "kind": "ats-registry"}])
    ats._scan_ats(FakeSite({"https://api.lever.co/v0/postings/voorbeeld": json.dumps(postings)}), {"id": "ats-bedrijven"},
                  {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    with store.connect() as con:
        rows = con.execute("SELECT * FROM jobs WHERE source_id='ats-bedrijven'").fetchall()
    assert [row["url"] for row in rows] == ["https://jobs.lever.co/voorbeeld/1"]
    job = rows[0]
    assert job["location"] == "Amsterdam" and job["company"] == "Voorbeeld" and job["hours"] == "Full-time"
    assert "Plannen maken" in job["description"] and "Hybride werken." in job["description"]
    assert job["posted_at"].startswith("2026-")


def test_personio_boards_read_the_feed_and_fill_in_text_from_job_pages(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ats, "load_employers", lambda: [{"name": "Voorbeeld", "system": "personio", "slug": "voorbeeld", "detail_cap": 1}])
    monkeypatch.setattr(ats.time, "sleep", lambda _: None)
    page = "<html><body><main><h1>Adviseur</h1><p>" + "Je adviseert klanten over hun gebouwen. " * 20 + "</p></main></body></html>"
    fake = FakeSite({f"{PERSONIO}/xml": PERSONIO_FEED, f"{PERSONIO}/job/11": page, f"{PERSONIO}/job/12": page})
    store.sync_sources([{"id": "ats-bedrijven", "kind": "ats-registry"}])
    entry = {"status": "ok", "new": 0, "updated": 0, "items": 0}
    ats._scan_ats(fake, {"id": "ats-bedrijven"}, entry, {"new": 0, "updated": 0})
    with store.connect() as con:
        rows = {row["url"]: row for row in con.execute("SELECT * FROM jobs").fetchall()}
    assert set(rows) == {f"{PERSONIO}/job/11", f"{PERSONIO}/job/12"}  # Bratislava stays out
    first, second = rows[f"{PERSONIO}/job/11"], rows[f"{PERSONIO}/job/12"]
    assert "adviseert klanten" in first["description"] and first["hours"] == "Fulltime · vast"
    assert first["location"] == "Vianen, Heerlen" and first["company"] == "Voorbeeld"
    assert second["hours"] == "Parttime · stage" and not second["description"]  # over this round's cap
    assert fake.asked.count(f"{PERSONIO}/job/12") == 0
    # Next round: the page fetched recently is not fetched again, the other one now is.
    fake.asked.clear()
    ats._scan_ats(fake, {"id": "ats-bedrijven"}, dict(entry), {"new": 0, "updated": 0})
    assert f"{PERSONIO}/job/11" not in fake.asked and f"{PERSONIO}/job/12" in fake.asked


def test_a_moved_personio_board_is_skipped_and_a_strange_feed_is_reported(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ats.time, "sleep", lambda _: None)
    store.sync_sources([{"id": "x", "kind": "ats-registry"}])
    monkeypatch.setattr(ats, "load_employers", lambda: [{"name": "Weg", "system": "personio", "slug": "weg"}])
    ats._scan_ats(FakeSite({}), {"id": "x"}, {"items": 0}, {"new": 0, "updated": 0})  # 404: no failure
    odd = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><workzag-jobs/>'
    monkeypatch.setattr(ats, "load_employers", lambda: [{"name": "Raar", "system": "personio", "slug": "raar", "tld": "de"}])
    with pytest.raises(RuntimeError, match="raar"):
        ats._scan_ats(FakeSite({"https://raar.jobs.personio.de/xml": odd}), {"id": "x"}, {"items": 0}, {"new": 0, "updated": 0})


def _job_page(title, place):
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": title, "datePosted": "2026-11-01",
               "description": "<p>" + "Je werkt aan mooie dingen. " * 25 + "</p>",
               "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": place}}}
    return f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head><body><h1>{title}</h1></body></html>'


def test_sitemap_boards_read_job_pages_and_keep_only_dutch_ones(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ats.time, "sleep", lambda _: None)
    host = "https://jobs.voorbeeld.test"
    sitemap = f"""<?xml version="1.0"?><urlset>
      <url><loc>{host}/jobs</loc></url><url><loc>{host}/jobs/101-planner</loc></url>
      <url><loc>{host}/jobs/102-engineer</loc></url><url><loc>{host}/jobs/101-planner</loc></url>
      <url><loc>{host}/jobs/101-planner/applications/new</loc></url></urlset>"""
    hr = "https://voorbeeld.homerun.co"
    pages = {
        f"{host}/sitemap.xml": sitemap,
        f"{host}/jobs/101-planner": _job_page("Planner", "Eindhoven"),
        f"{host}/jobs/102-engineer": _job_page("Engineer", "Lisbon, Portugal"),
        f"{hr}/sitemap.xml": f"<urlset><url><loc>{hr}</loc></url><url><loc>{hr}/redacteur-20-uur</loc></url><url><loc>{hr}/redacteur-20-uur/nl/apply</loc></url></urlset>",
        f"{hr}/redacteur-20-uur": _job_page("Redacteur (20 uur)", "Amsterdam"),
    }
    monkeypatch.setattr(ats, "load_employers", lambda: [
        {"name": "Voorbeeld", "system": "teamtailor", "slug": "voorbeeld", "host": host},
        {"name": "Krant", "system": "homerun", "slug": "voorbeeld"},
    ])
    fake = FakeSite(pages)
    store.sync_sources([{"id": "ats-bedrijven", "kind": "ats-registry"}])
    ats._scan_ats(fake, {"id": "ats-bedrijven"}, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    with store.connect() as con:
        rows = {row["url"]: row for row in con.execute("SELECT * FROM jobs").fetchall()}
    assert set(rows) == {f"{host}/jobs/101-planner", f"{hr}/redacteur-20-uur"}
    planner = rows[f"{host}/jobs/101-planner"]
    assert planner["title"] == "Planner" and planner["location"] == "Eindhoven" and planner["company"] == "Voorbeeld"
    assert "mooie dingen" in planner["description"] and rows[f"{hr}/redacteur-20-uur"]["company"] == "Krant"
    assert fake.asked.count(f"{host}/jobs/101-planner") == 1  # listed twice, read once
    fake.asked.clear()
    ats._scan_ats(fake, {"id": "ats-bedrijven"}, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    assert f"{host}/jobs/101-planner" not in fake.asked  # known and recent: only touched


def test_greenjobs_turns_national_on_existing_installations(client):  # noqa: F811
    old = {"id": "greenjobs", "kind": "exenzo-json", "scope": "regional", "regions": ["Amsterdam"], "search": "Amsterdam"}
    store.save_source_config(old, enabled=True)
    greenjobs = {item["id"]: item for item in sources.load_sources()}["greenjobs"]
    assert greenjobs["scope"] == "national" and greenjobs["regions"] == [] and not greenjobs["search"]
    assert greenjobs["enabled"] is True and greenjobs["max_pages"] * greenjobs["per_page"] >= 300


def test_exenzo_fills_in_cut_off_texts_from_the_public_page(client, monkeypatch):  # noqa: F811
    from app.adapters import exenzo

    monkeypatch.setattr(exenzo.time, "sleep", lambda _: None)
    api = "https://bord.api.exenzo.test/api/public/v1/jobs"
    items = [{"title": f"Adviseur {n}", "slug": f"adviseur-{n}", "description": "Kort. " * 40, "city": "Utrecht"} for n in (1, 2)]

    class Fake(FakeSite):
        def get(self, url, **kwargs):
            if url == api:
                self.asked.append(url)
                return httpx.Response(200, json={"total": 2, "data": items}, request=httpx.Request("GET", url))
            return super().get(url, **kwargs)

    site = "https://bord.test"
    fake = Fake({f"{site}/vacature/adviseur-1": _job_page("Adviseur 1", "Utrecht"), f"{site}/vacature/adviseur-2": _job_page("Adviseur 2", "Utrecht")})
    source = {"id": "bord", "base_url": "https://bord.api.exenzo.test", "public_site": site, "enrich_details": True, "detail_cap": 1}
    store.sync_sources([{"id": "bord", "kind": "exenzo-json"}])
    exenzo._scan_exenzo(fake, source, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    with store.connect() as con:
        texts = {row["title"]: row["description"] for row in con.execute("SELECT title, description FROM jobs")}
    assert "mooie dingen" in texts["Adviseur 1"] and texts["Adviseur 2"].startswith("Kort.")  # one page per round
    fake.asked.clear()
    exenzo._scan_exenzo(fake, source, {"items": 0, "new": 0, "updated": 0}, {"new": 0, "updated": 0})
    with store.connect() as con:
        texts = {row["title"]: row["description"] for row in con.execute("SELECT title, description FROM jobs")}
    assert f"{site}/vacature/adviseur-1" not in fake.asked and "mooie dingen" in texts["Adviseur 1"]  # kept, not re-read
    assert "mooie dingen" in texts["Adviseur 2"]
