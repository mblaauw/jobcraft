"""The 3-minute start: CV reading without AI, free wishes, the four steps, three questions."""

import io
import json

import pytest
from docx import Document

from app import cv_text, onboarding, profile, ranking, runtime, store, tenancy
from app.routers import start as start_routes
from app.scoring import digests, wishes
from tests.test_jobs_page import client, soup  # noqa: F401

CV = """Sam de Vries
Lindelaan 12
1234 AB Utrecht
sam.devries@example.test
06-12345678
linkedin.com/in/samdevries
Geboortedatum: 01-02-1985

Profiel
Sam de Vries is een rustige projectleider die graag met gemeenten werkt.

Werkervaring
2019 - heden Projectleider bij Gemeente Utrecht
Leidt projecten in de openbare ruimte.
2012 - 2019
Beleidsmedewerker | Provincie Gelderland

Vaardigheden
Stakeholdermanagement, Planning, Excel

Opleiding
Bestuurskunde, Universiteit Utrecht

Referenties
Jan Jansen, 06-87654321
"""


def _pdf(text: str) -> bytes:
    """A minimal real PDF with one text line per input line."""
    lines = []
    for line in text.splitlines():
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines.append(f"({escaped}) Tj T*")
    stream = "BT /F1 10 Tf 14 TL 40 780 Td " + " ".join(lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n{body}\nendobj\n"
    table = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    output += "".join(f"{offset:010} 00000 n \n" for offset in offsets)
    output += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{table}\n%%EOF"
    return output.encode("latin-1")


def _docx(text: str) -> bytes:
    document = Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _add_jobs(count=14):
    for number in range(count):
        store.upsert_job(
            {
                "source_id": "test",
                "url": f"https://example.test/{number}",
                "title": f"Functie{number} projectleider",
                "company": f"Bedrijf {number}",
                "description": f"Werk nummer {number} " * 10,
                "location": "unknown",
                "content_hash": f"hash{number}",
            }
        )


@pytest.fixture()
def flow(client, monkeypatch):  # noqa: F811
    """The jobs-page fixture replaces ranking pieces; this flow needs the real rerank."""
    monkeypatch.setattr(ranking, "rerank_all", lambda *args: 0)
    monkeypatch.setattr(start_routes.feedback, "rebuild", lambda: None)
    return client


@pytest.mark.parametrize("build,name", [(_pdf, "cv.pdf"), (_docx, "cv.docx")])
def test_both_formats_give_the_same_reading(build, name):
    found = cv_text.read_cv(cv_text.extract_text(build(CV), name), "Sam de Vries")
    assert found["roles"] == ["Projectleider", "Beleidsmedewerker"]
    assert found["skills"] == ["Stakeholdermanagement", "Planning", "Excel"]
    assert found["seniority"] == "senior"
    assert found["place"] == "Utrecht"


def test_identity_never_reaches_the_profile_text():
    found = cv_text.read_cv(CV, "Sam de Vries")
    text = "\n".join(found["sections"].values())
    for private in ("Sam", "Vries", "Lindelaan", "1234", "example.test", "06-", "linkedin", "1985", "Jan Jansen"):
        assert private not in text
    assert "projectleider" in text


def test_redact_keeps_year_ranges_and_removes_contact_details():
    text = cv_text.redact("2012 - 2019 Adviseur\nBel +31 6 12345678 of mail a@b.nl\nStationsstraat 4")
    assert "2012 - 2019 Adviseur" in text
    assert "12345678" not in text and "a@b.nl" not in text and "Stationsstraat" not in text


@pytest.mark.parametrize(
    "raw,name,message",
    [
        (b"not a pdf", "cv.pdf", "niet lezen"),
        (b"not a zip", "cv.docx", "niet lezen"),
        (b"x", "cv.txt", "PDF- of DOCX"),
    ],
)
def test_bad_files_get_a_plain_message(raw, name, message):
    with pytest.raises(ValueError, match=message):
        cv_text.extract_text(raw, name)


def test_scan_without_text_is_explained():
    with pytest.raises(ValueError, match="geen leesbare tekst"):
        cv_text.extract_text(_pdf(" "), "scan.pdf")


def test_wishes_never_change_the_verdict_digest_or_public_preferences():
    base = profile._validate_preferences({}).model_dump()
    changed = {**base, "wishes": {**base["wishes"], "salary_min": 5000, "remote": "remote"}}
    assert digests.profile_hash(base) == digests.profile_hash(changed)
    assert "wishes" not in profile.public_preferences(changed)
    assert "5000" not in json.dumps(profile.public_preferences(changed))


def test_adding_the_wishes_field_keeps_existing_verdicts_valid():
    stored = profile._validate_preferences({}).model_dump()
    without = {key: value for key, value in stored.items() if key != "wishes"}
    assert digests.profile_hash(stored) == digests.profile_hash(without)


def test_consolidation_draft_keeps_wishes(client):  # noqa: F811
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    with tenancy.scope({"id": owner}):
        preferences = profile.read_preferences()
        preferences["wishes"]["salary_min"] = 4000
        profile.write_preferences(preferences)
        path = profile.save_draft("## Over mij\nTekst", {"tracks": []})
        assert json.loads(path.read_text())["preferences"]["wishes"]["salary_min"] == 4000


@pytest.mark.parametrize(
    "text,title,wish,sign",
    [
        ("salaris € 2.800 per maand", "x", {"salary_min": 4000}, -1),
        ("salaris € 4.500 per maand", "x", {"salary_min": 4000}, 1),
        ("€ 70.000 per jaar", "x", {"salary_min": 4000}, 1),
        ("een leuke baan", "x", {"salary_min": 4000}, 0),
        ("deels thuis werken", "x", {"remote": "hybrid"}, 1),
        ("geen thuiswerk mogelijk", "x", {"remote": "remote"}, -1),
        ("vast contract", "x", {"contracts": ["vast"]}, 1),
        ("freelance opdracht", "x", {"contracts": ["vast"]}, -1),
        ("tekst", "Senior adviseur", {"seniority": ["junior"]}, -1),
        ("tekst", "Junior adviseur", {"seniority": ["junior"]}, 1),
        ("tekst", "Adviseur", {"seniority": ["junior"]}, 0),
    ],
)
def test_wish_rules_only_act_on_what_a_vacancy_says(text, title, wish, sign):
    delta, _notes = wishes.adjust(text, title, wish)
    assert (delta > 0) - (delta < 0) == sign


def test_new_account_lands_on_the_start(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tenancy, "LEGACY_ID", "not-this-account")
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/start"
    page = client.get("/start", follow_redirects=False)
    assert page.status_code == 200
    assert "ongeveer 3 minuten" in page.text
    assert 'href="/start/cv"' in page.text


def test_cv_upload_shows_corrections_and_stores_only_what_is_confirmed(flow):
    page = flow.post("/start/cv", files={"cv": ("cv.pdf", _pdf(CV), "application/pdf")})
    html = soup(page)
    assert [item["value"] for item in html.select('input[name="role"]')] == [
        "Projectleider",
        "Beleidsmedewerker",
    ]
    assert "sam.devries" not in page.text
    response = flow.post(
        "/start/cv/confirm",
        data={
            "role": ["Projectleider"],
            "more_roles": "Adviseur water",
            "skill": ["Planning"],
            "seniority": "senior",
            "place": "Utrecht",
            "section_summary": "Rustige projectleider die graag met gemeenten werkt.",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/start/verder"
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    with tenancy.scope({"id": owner}):
        preferences = profile.read_preferences()
        assert [track["name"] for track in preferences["tracks"]] == ["Projectleider", "Adviseur water"]
        assert preferences["keywords"]["boost"] == ["Planning"]
        assert preferences["wishes"]["seniority"] == ["senior"]
        assert preferences["home"]["label"] == "Utrecht"
        assert "## Over mij" in profile.read_profile()


def test_bad_upload_returns_to_the_form_with_a_message(flow):
    html = soup(flow.post("/start/cv", files={"cv": ("cv.pdf", b"nope", "application/pdf")}))
    assert "niet lezen" in html.select_one(".banner").get_text()
    assert html.select_one('input[type="file"]')


def test_count_follows_the_choices(flow):
    _add_jobs()
    store.upsert_job(
        {
            "source_id": "test",
            "url": "https://example.test/senior",
            "title": "Senior kok",
            "company": "Keuken",
            "description": "Koken zonder thuiswerk",
            "location": "unknown",
            "content_hash": "kok",
        }
    )
    def count(**data):
        return flow.post("/start/keuzes/teller", data=data).json()["count"]

    assert count() == 15
    assert count(roles="projectleider") == 14
    assert count(roles="kok") == 1
    assert count(veto="koken") == 14
    assert count(seniority="junior") == 14
    data = flow.post("/start/keuzes/teller", data={"place": "Atlantis"}).json()
    assert data["place_known"] is False


def _choices(flow):
    return flow.post(
        "/start/keuzes",
        data={
            "roles": "Projectleider",
            "place": "Utrecht",
            "minutes": "45",
            "hours": "24-32",
            "remote": "hybrid",
            "salary": "4000",
            "contract": ["vast"],
            "no_phone_main": "1",
        },
        follow_redirects=False,
    )


def test_choices_are_saved_and_lead_to_varied_real_vacancies(flow):
    _add_jobs()
    assert _choices(flow).headers["location"] == "/start/vacatures"
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    with tenancy.scope({"id": owner}):
        preferences = profile.read_preferences()
        assert preferences["hard_filters"]["hours_min"] == 24
        assert preferences["hard_filters"]["no_phone_main"] is True
        assert preferences["wishes"] == {
            "salary_min": 4000,
            "remote": "hybrid",
            "contracts": ["vast"],
            "seniority": [],
        }
        assert preferences["home"]["bike_minutes"] == 45
        assert profile.profile_exists()
        queue = json.loads(store.meta_get("start_swipe"))
        companies = {store.get_job(job_id)["company"] for job_id in queue}
    assert len(queue) == 5
    assert len(companies) == 5


def test_swipes_feed_votes_and_lead_to_the_questions(flow):
    _add_jobs()
    _choices(flow)
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    with tenancy.scope({"id": owner}):
        queue = json.loads(store.meta_get("start_swipe"))
    assert "Vacature 1 van 5" in soup(flow.get("/start/vacatures")).get_text()
    answers = ["ja", "nee"] + ["overslaan"] * 3
    for job_id, answer in zip(queue, answers, strict=True):
        flow.post(f"/start/vacatures/{job_id}", data={"answer": answer}, follow_redirects=False)
    with tenancy.scope({"id": owner}):
        assert store.get_job(queue[0])["vote"] == 1
        assert store.get_job(queue[1])["vote"] == -1
        assert store.get_job(queue[2])["vote"] == 0
    assert flow.get("/start/vacatures", follow_redirects=False).headers["location"] == "/start/vragen"


def test_empty_catalogue_skips_the_swipes(flow):
    _choices(flow)
    assert flow.get("/start/vacatures", follow_redirects=False).headers["location"] == "/start/vragen"


class FakeManager:
    client = object()

    def __init__(self, fail_at=None):
        self.prompts = []
        self.fail_at = fail_at

    def generate(self, prompt, *, agent):
        assert agent == "onboarding"
        self.prompts.append(prompt)
        if self.fail_at == len(self.prompts):
            raise RuntimeError("provider down")
        return f"Vraag {len(self.prompts)}?", {"input_tokens": 5, "output_tokens": 2, "cost_usd": 0.001}


def _begin_questions(flow):
    with store.connect() as con:
        owner = con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]
    with tenancy.scope({"id": owner}):
        profile.merge_sections(
            {"Over mij": "Rustig.", "Privé": "GEHEIM_MEDISCH", "Open vragen": "GEHEIM_OPEN"}
        )
    flow.post("/start/vragen/volgende")
    return owner


def test_exactly_three_questions_then_it_stops_by_itself(flow, monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(runtime, "manager", manager)
    owner = _begin_questions(flow)
    assert "Vraag 1 van 3" in soup(flow.get("/start/vragen")).get_text()
    flow.post("/start/vragen/antwoord", data={"answer": "Antwoord een"})
    assert "Vraag 2 van 3" in soup(flow.get("/start/vragen")).get_text()
    flow.post("/start/vragen/antwoord", data={"answer": "Antwoord twee"})
    response = flow.post(
        "/start/vragen/antwoord", data={"answer": "Antwoord drie"}, follow_redirects=False
    )
    assert response.headers["location"] == "/jobs?welkom=1"
    assert len(manager.prompts) == 3
    with tenancy.scope({"id": owner}):
        text = profile.read_profile()
        assert "## Uit het gesprek" in text and "Antwoord drie" in text
        assert store.meta_get("counter:profile_finished") == "1"
        assert store.usage_total()["calls"] == 3
    with store.connect() as con:
        kinds = [row["kind"] for row in con.execute("SELECT kind FROM tasks WHERE owner=?", (owner,))]
    assert "match" in kinds


def test_questions_only_see_public_profile_text(flow, monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(runtime, "manager", manager)
    _begin_questions(flow)
    assert "Rustig." in manager.prompts[0]
    assert "GEHEIM" not in manager.prompts[0]


def test_ai_failure_never_blocks_the_start(flow, monkeypatch):
    monkeypatch.setattr(runtime, "manager", FakeManager(fail_at=2))
    _begin_questions(flow)
    response = flow.post("/start/vragen/antwoord", data={"answer": "Een"}, follow_redirects=False)
    assert response.headers["location"] == "/jobs?welkom=1"
    monkeypatch.setattr(runtime, "manager", None)
    response = flow.post("/start/vragen/volgende", follow_redirects=False)
    assert response.headers["location"] == "/start/vragen?niet=1"


def test_no_ai_call_once_the_budget_is_spent(flow, monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(runtime, "manager", manager)
    monkeypatch.setattr(onboarding, "_budget_reached", lambda: True)
    flow.post("/start/vragen/volgende")
    assert not manager.prompts


def test_opening_the_questions_page_never_spends(flow, monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(runtime, "manager", manager)
    assert flow.get("/start/vragen").status_code == 200
    assert not manager.prompts


def test_later_edits_return_to_the_list(flow):
    store.meta_set("start_finished_at", "2026-11-01")
    _add_jobs(1)
    assert _choices(flow).headers["location"] == "/jobs"


def test_redoing_the_choices_restarts_the_vacancy_count(flow):
    _add_jobs()
    store.meta_set("start_swipe_done", "10")
    _choices(flow)
    assert "Vacature 1 van 5" in soup(flow.get("/start/vacatures")).get_text()


def test_a_pdf_cv_is_enough_to_make_documents_later(flow, monkeypatch):
    monkeypatch.setattr(tenancy, "LEGACY_ID", "not-this-account")
    assert flow.post("/start/cv", files={"cv": ("cv.pdf", _pdf(CV), "application/pdf")}).status_code == 200
    flow.post(
        "/start/cv/confirm",
        data={"role": ["Projectleider"], "section_experience": "Projectleider bij Acme"},
    )
    job_id, _ = store.upsert_job(
        {"source_id": "test", "url": "https://example.test/doc", "title": "Planner", "description": "Tekst", "content_hash": "doc"}
    )
    page = flow.get(f"/jobs/{job_id}").text
    assert "Maak je sollicitatie" in page and "Maak eerst je profiel" not in page


def test_every_step_says_how_long_and_what_for(flow):
    for path, step in (("/start/cv", "Stap 1 van 4"), ("/start/keuzes", "Stap 2 van 4"), ("/start/vragen", "Stap 4 van 4")):
        text = soup(flow.get(path)).get_text()
        assert step in text and "ongeveer" in text


def test_a_pause_returns_to_the_first_unfinished_step(flow, monkeypatch):
    monkeypatch.setattr(tenancy, "LEGACY_ID", "not-this-account")
    _add_jobs()
    flow.post("/start/cv/confirm", data={"role": ["Planner"], "section_summary": "Rustige planner met ervaring."})
    assert flow.get("/start", follow_redirects=False).headers["location"] == "/start/verder"
    _choices(flow)
    assert flow.get("/start", follow_redirects=False).headers["location"] == "/start/vacatures"
    assert "Ga verder waar je gebleven was" in flow.get("/").text
    flow.post("/start/vacatures/stoppen", follow_redirects=False)
    assert flow.get("/start", follow_redirects=False).headers["location"] == "/start/vragen"
    assert flow.post("/start/klaar", follow_redirects=False).headers["location"] == "/jobs?welkom=1"
    assert flow.get("/start", follow_redirects=False).headers["location"] == "/jobs"
    assert "Ga verder waar je gebleven was" not in flow.get("/").text


def test_own_vacancy_skips_the_questions_and_any_assessment(flow, monkeypatch):
    monkeypatch.setattr(tenancy, "LEGACY_ID", "not-this-account")
    calls = []
    monkeypatch.setattr(start_routes, "_assess_soon", lambda *args: calls.append(args))
    flow.post("/start/cv/confirm", data={"role": ["Planner"], "section_summary": "Rustige planner met ervaring."})
    assert "Ik heb zelf een vacature" in flow.get("/start/verder").text
    response = flow.post("/start/zelf", follow_redirects=False)
    assert response.headers["location"] == "/jobs/toevoegen"
    assert calls == []
    assert flow.get("/start", follow_redirects=False).headers["location"] == "/jobs"


def test_the_old_chat_is_gone_for_normal_accounts(flow, monkeypatch):
    monkeypatch.setattr(tenancy, "LEGACY_ID", "not-this-account")
    response = flow.get("/interview", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_existing_accounts_see_what_is_new_on_the_overview(flow, monkeypatch):
    monkeypatch.setattr(tenancy, "LEGACY_ID", "not-this-account")
    flow.post("/start/cv/confirm", data={"role": ["Planner"], "section_summary": "Rustige planner met ervaring."})
    page = flow.get("/").text
    assert "Ga verder waar je gebleven was" in page
    assert "/jobs/toevoegen" in page
    assert "voeg je eigen Word-CV toe" in page
    store_votes = flow.get("/jobs?within=0")
    assert store_votes.status_code == 200
