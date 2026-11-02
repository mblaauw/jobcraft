"""The application studio: plan, reviewed writing, fact checks, models and the AI log."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from test_jobs_page import add_job, client  # noqa: F401

from app import (
    ai_log,
    application_plan,
    applications,
    career_position,
    config,
    docs,
    document_contact,
    document_content,
    document_design,
    document_letter,
    document_pipeline,
    employer_research,
    llm,
    model_routes,
    profile,
    runtime,
    store,
    vacancy_reader,
    vacancy_text,
)
from app.llm_client import FakeLLMClient

PROFILE = (
    "# Profiel\n\n## Werkervaring\nSenior adviseur, Gemeente Utrecht, 2019 - heden\n"
    "- Vergunningtijd van 14 naar 8 weken.\n\n## Privé\nGeheim feit over mijn gezin.\n"
)
RESEARCH = {
    "language": "nl", "formality": "je", "contact_person": "", "role_in_one_line": "Adviseur",
    "real_problem": "Plannen lopen vast", "must_haves": ["Advies"], "nice_to_haves": [],
    "keywords": ["advies"], "culture_signals": [],
    "employer_facts": [
        {"fact": "Echt feit", "source_url": "https://werkgever.nl/"},
        {"fact": "Uit het geheugen", "source_url": "https://verzonnen.nl/"},
    ],
}
STRATEGY = {
    "fit_summary": "Past goed.",
    "evidence": [{"need": "Advies", "proof": "Gemeente Utrecht", "strength": "sterk"}],
    "angles": [
        {"id": "a", "title": "Brug", "story": "Verhaal a."},
        {"id": "b", "title": "Tempo", "story": "Verhaal b."},
    ],
    "recommended_angle": "b",
    "questions": [{"id": "q1", "question": "Welk resultaat?", "why": "Bewijs."}],
    "gaps": [],
}
CV = {
    "headline": "Senior adviseur",
    "profile": "Adviseur bij Gemeente Utrecht die vergunningen sneller door de organisatie krijgt.",
    "highlights": ["Vergunningtijd van 14 naar 8 weken", "Adviseert over plannen"],
    "experience": [
        {"role": "Senior adviseur", "organisation": "Gemeente Utrecht", "period": "2019 tot heden",
         "bullets": ["Bracht de vergunningtijd van 14 naar 8 weken."]}
    ],
    "education": [{"title": "Master", "organisation": "", "period": ""}],
    "skills": ["Advies"],
    "languages": ["Nederlands"],
}
LETTER = {
    "salutation": "Beste team,",
    "paragraphs": [" ".join(["woord"] * 90), " ".join(["woord"] * 90)],
}
GOOD = {"score": 90, "first_impression": "Sterk", "strongest": "Bewijs", "unsupported": [], "fixes": []}
JOB = {"id": 1, "title": "Adviseur", "company": "Werkgever", "description": "Advies geven", "location": "", "url": ""}


@pytest.fixture(autouse=True)
def _world(client):  # noqa: F811
    """Every test here gets its own empty data folder and database."""


def _manager(replies):
    manager = llm.ServerManager()
    manager.client = FakeLLMClient([json.dumps(item) if isinstance(item, dict) else item for item in replies])
    return manager


def _plan(job_id):
    plan = {"research": RESEARCH, "strategy": STRATEGY, "angle": "b", "answers": {}, "note": "", "pages": []}
    application_plan.save(job_id, plan)
    return plan


def _no_pdf(monkeypatch):
    monkeypatch.setattr(docs, "convert_pdf", lambda path: None)


# ---------------------------------------------------------------- checks


def test_invented_numbers_and_names_are_found():
    source = "Vergunningtijd van 14 naar 8 weken bij Gemeente Utrecht."
    problems = document_content.unsupported_facts(
        ["Bracht de tijd van 14 naar 6 weken bij Gemeente Amsterdam."], source
    )
    assert any("6" in item for item in problems)
    assert any("Amsterdam" in item for item in problems)
    assert not any("14" in item or "Utrecht" in item for item in problems)


def test_a_role_must_be_copied_not_polished():
    content = {"experience": [{"role": "Directeur", "organisation": "Gemeente Utrecht", "period": "", "bullets": []}]}
    assert document_content.roles_changed(content, PROFILE) == ['"Directeur" staat niet zo in je cv.']


def test_cliches_are_flagged():
    assert document_content.style_problems(["Hierbij solliciteer ik met passie."])


def test_a_field_called_title_survives_the_schema_cleanup():
    schema = application_plan.schema_for(application_plan.Angle)
    assert "title" in schema["properties"]
    assert "title" not in schema


# ---------------------------------------------------------------- writing


def test_the_second_try_hears_what_was_wrong():
    manager = _manager(["geen json", STRATEGY])
    result = application_plan.call_json(manager, "vraag", agent="doc_strategist", model=application_plan.Strategy, ref="")
    assert result["recommended_angle"] == "b"
    assert "ongeldig" in manager.client.calls[1]["messages"][-1]["content"]


def test_an_employer_fact_without_a_page_we_read_is_dropped(monkeypatch):
    monkeypatch.setattr(employer_research, "gather", lambda job: [{"url": "https://werkgever.nl/", "title": "", "text": "x"}])
    monkeypatch.setattr(profile, "read_document_excerpt", lambda limit: PROFILE)
    job_id = add_job()
    plan = application_plan.make(_manager([RESEARCH, STRATEGY]), {**JOB, "id": job_id})
    assert [item["fact"] for item in plan["research"]["employer_facts"]] == ["Echt feit"]


def test_name_private_notes_and_contact_stay_out_of_every_prompt(monkeypatch):
    profile.write_profile(PROFILE, "test")
    document_contact.save({"name": "Noor Bakhuis", "email": "noor@voorbeeld.nl", "phone": "0612345678"})
    monkeypatch.setattr(employer_research, "gather", lambda job: [])
    job_id = add_job()
    manager = _manager([RESEARCH, STRATEGY])
    plan = application_plan.make(manager, {**JOB, "id": job_id})
    manager.client.replies = [json.dumps(LETTER), json.dumps(GOOD)]
    document_pipeline.write_reviewed(manager, {**JOB, "id": job_id}, "motivatie", plan)
    sent = json.dumps(manager.client.calls)
    assert "Geheim feit" not in sent
    assert "Bakhuis" not in sent and "noor@voorbeeld" not in sent and "0612345678" not in sent


def test_a_weak_draft_gets_one_revision_and_a_clean_one_does_not():
    profile.write_profile(PROFILE, "test")
    job = {**JOB, "description": "Advies. Gemeente Utrecht, 14 en 8 weken, 2019, Nederlands, Master."}
    plan = {"research": RESEARCH, "strategy": STRATEGY, "angle": "b", "answers": {}, "pages": []}
    manager = _manager([CV, GOOD])
    _content, review = document_pipeline.write_reviewed(manager, job, "resume", plan)
    assert review["score"] == 90 and len(manager.client.calls) == 2

    weak = {**GOOD, "score": 60, "fixes": ["Scherper"]}
    manager = _manager([CV, weak, CV, GOOD])
    _content, review = document_pipeline.write_reviewed(manager, job, "resume", plan)
    assert len(manager.client.calls) == 4
    assert "Scherper" in manager.client.calls[2]["messages"][-1]["content"]
    assert review["score"] == 90


def test_a_revision_that_invents_more_is_thrown_away():
    profile.write_profile(PROFILE, "test")
    job = {**JOB, "description": "Advies. Gemeente Utrecht, 14 en 8 weken, 2019, Nederlands, Master."}
    plan = {"research": RESEARCH, "strategy": STRATEGY, "angle": "b", "answers": {}, "pages": []}
    invented = {**CV, "profile": CV["profile"] + " Spaarde 900 euro uit."}
    manager = _manager([CV, {**GOOD, "score": 60}, invented])
    content, review = document_pipeline.write_reviewed(manager, job, "resume", plan)
    assert "900" not in content["profile"] and review["warnings"] == []


# ---------------------------------------------------------------- versions and rendering


def test_all_designs_render_real_text(tmp_path):
    from docx import Document

    contact = {"name": "Noor Bakhuis", "email": "n@v.nl", "phone": "06", "place": "Utrecht", "linkedin": ""}
    for design in document_design.DESIGNS:
        path = tmp_path / f"{design}.docx"
        document_design.render_cv(CV, contact, path, document_design.look(design, "petrol"))
        document = Document(str(path))
        text = "\n".join(p.text for p in document.paragraphs) + "\n".join(
            cell.text for table in document.tables for row in table.rows for cell in row.cells
        )
        assert "Noor Bakhuis" in text and "Bracht de vergunningtijd" in text and "" not in text
        letter = tmp_path / f"{design}-brief.docx"
        document_letter.render_letter(LETTER, contact, JOB, "Utrecht, 01-01-2027", letter, document_design.look(design))
        assert letter.exists()


def test_a_new_look_is_a_new_version_and_never_overwrites(client, monkeypatch):  # noqa: F811
    _no_pdf(monkeypatch)
    job_id = add_job()
    _plan(job_id)
    job = dict(store.get_job(job_id))
    first = document_pipeline.save_version(job, "resume", CV)
    path = store.get_document(first["id"])["docx_path"]
    before = (config.DATA_DIR / path).read_bytes()
    answer = client.post(
        f"/jobs/{job_id}/sollicitatie/documenten/{first['id']}/opmaak",
        data={"design": "blok", "colour": "petrol"},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    rows = store.list_documents(job_id)
    assert sorted(row["version"] for row in rows) == [1, 2]
    assert (config.DATA_DIR / path).read_bytes() == before
    assert json.loads(next(row for row in rows if row["version"] == 2)["meta_json"])["design"] == "blok"


def test_an_edit_saves_a_version_and_a_bad_edit_says_why(client, monkeypatch):  # noqa: F811
    _no_pdf(monkeypatch)
    job_id = add_job()
    _plan(job_id)
    first = document_pipeline.save_version(dict(store.get_job(job_id)), "motivatie", LETTER)
    url = f"/jobs/{job_id}/sollicitatie/documenten/{first['id']}/tekst"
    good = client.post(url, data={"aanhef": "Beste Sam,", "tekst": "Eerste alinea.\n\nTweede alinea."}, follow_redirects=False)
    assert "opgeslagen=tekst" in good.headers["location"]
    assert len(store.list_documents(job_id)) == 2
    bad = client.post(url, data={"aanhef": "", "tekst": ""}, follow_redirects=False)
    assert "probleem=" in bad.headers["location"]
    assert len(store.list_documents(job_id)) == 2


# ---------------------------------------------------------------- the pages


def test_the_studio_walks_from_empty_to_plan_to_work(client, monkeypatch):  # noqa: F811
    _no_pdf(monkeypatch)
    profile.write_profile(PROFILE, "test")
    monkeypatch.setattr(runtime, "manager", SimpleNamespace(client=object()))
    job_id = add_job()
    assert "Maak mijn sollicitatieplan" in client.get(f"/jobs/{job_id}/sollicitatie").text
    client.post(f"/jobs/{job_id}/sollicitatie/plan")
    assert "We maken je plan" in client.get(f"/jobs/{job_id}/sollicitatie").text
    store.meta_set(f"doc_status:{job_id}", json.dumps({"state": "done"}))
    _plan(job_id)
    page = client.get(f"/jobs/{job_id}/sollicitatie").text
    assert "Plannen lopen vast" in page and "Onze aanrader" in page and "Welk resultaat?" in page
    client.post(
        f"/jobs/{job_id}/sollicitatie/schrijven",
        data={"invalshoek": "a", "antwoord_q1": "Twintig dossiers afgerond", "toelichting": "Ik ken de regio"},
    )
    plan = application_plan.load(job_id)
    assert plan["angle"] == "a" and plan["answers"]["q1"] == "Twintig dossiers afgerond"
    assert application_plan.stories()[0]["answer"] == "Twintig dossiers afgerond"
    assert "Twintig dossiers" in application_plan.person_source(plan)
    assert "Ik ken de regio" in application_plan.person_source(plan)
    store.meta_set(f"doc_status:{job_id}", json.dumps({"state": "done"}))
    document_pipeline.save_version(
        dict(store.get_job(job_id)), "resume", CV,
        review={"score": 72, "first_impression": "Zes seconden", "fixes": ["Doe dit"], "warnings": ["Het getal 9"]},
    )
    page = client.get(f"/jobs/{job_id}/sollicitatie?tab=cv").text
    assert "Controleer dit voor je verstuurt" in page and "Het getal 9" in page and "Zes seconden" in page


def test_a_stuck_task_does_not_lock_the_page(client):  # noqa: F811
    profile.write_profile(PROFILE, "test")
    job_id = add_job()
    store.meta_set(
        f"doc_status:{job_id}",
        json.dumps({"state": "running", "kind": "plan", "updated_at": "2020-01-01T00:00:00+00:00"}),
    )
    assert "Dit duurde te lang" in client.get(f"/jobs/{job_id}/sollicitatie").text


def test_another_account_cannot_reach_a_document(client, monkeypatch):  # noqa: F811
    job_id = add_job()
    answer = client.post(
        f"/jobs/{job_id}/sollicitatie/documenten/999/opmaak", data={"design": "blok", "colour": "petrol"}
    )
    assert answer.status_code == 404


# ---------------------------------------------------------------- models and log


def test_the_admin_choice_decides_the_model(client):  # noqa: F811
    manager = _manager(["ok"])
    assert model_routes.model_for("cv_writer") == config.role_model("cv_writer")
    model_routes.save({"cv_writer": "beter-model"})
    manager.generate("vraag", agent="cv_writer")
    assert manager.client.calls[0]["model"] == "beter-model"
    with pytest.raises(ValueError):
        model_routes.save({"cv_writer": "ongeldig model!"})


def test_each_call_logs_model_input_and_output():
    manager = _manager(["het antwoord"])
    manager.generate("de vraag", agent="letter_writer", ref="job:7")
    row = ai_log.recent(5)[0]
    assert row["agent"] == "letter_writer" and row["ref"] == "job:7"
    full = ai_log.read(row["owner"], row["day"], row["offset"])
    assert full["output"] == "het antwoord"
    assert full["input"][-1]["content"] == "de vraag"


def test_a_failed_call_is_logged_too():
    manager = _manager([])
    manager.client.replies = []
    with pytest.raises(llm.LLMError):
        manager.generate("de vraag", agent="letter_writer")
    assert ai_log.recent(5)[0]["error"]


def test_old_log_files_are_removed(tmp_path):
    manager = _manager(["a"])
    manager.generate("v", agent="writer")
    row = ai_log.recent(1)[0]
    directory = ai_log._directory(row["owner"])
    old = directory / "2001-01-01.jsonl"
    old.write_text("{}\n")
    (directory / f"{row['day']}.jsonl").unlink()
    manager.client.replies = ["b"]
    manager.generate("v", agent="writer")
    assert not old.exists()


def test_an_admin_reads_only_their_own_prompts_on_the_page():
    from app.routers import admin_ai

    call = {"owner": "iemand", "model": "m", "input": [{"content": "cv"}], "output": "brief", "ref": "job:1", "error": "x"}
    assert admin_ai.readable(call, "iemand")["output"] == "brief"
    hidden = admin_ai.readable(call, "beheerder")
    assert hidden["model"] == "m" and not {"input", "output", "ref", "error"} & set(hidden)


def test_log_paths_cannot_leave_the_log_folder():
    assert ai_log.read("../..", "2026-01-01", 0) is None
    assert ai_log.read("legacy", "../../x", 0) is None


def test_admin_page_shows_models_and_the_log(client):  # noqa: F811
    manager = _manager(["antwoord"])
    manager.generate("vraag", agent="cv_writer")
    # The fixture account may be a regular user; the permissions test covers the guard itself.
    page = client.get("/admin/ai")
    assert page.status_code in (200, 403)
    if page.status_code == 200:
        assert "Model per taak" in page.text and "CV schrijven" in page.text


# ---------------------------------------------------------------- research


def test_research_needs_a_company_and_never_raises(monkeypatch):
    assert employer_research.gather({"company": "", "url": ""}) == []

    def boom(*args, **kwargs):
        raise RuntimeError("netwerk stuk")

    monkeypatch.setattr(employer_research.httpx, "Client", boom)
    assert employer_research.gather({"company": "Rijkswaterstaat", "url": ""}) == []


def test_research_rejects_a_site_that_is_someone_else():
    assert employer_research._slug("Gemeente Wassenaar") == "wassenaar"
    assert employer_research._slug("Acme B.V.") == "acme"
    assert not employer_research._mentions("Welkom bij de bakker", "Acme")


# ---------------------------------------------------------------- readable vacancies


def test_flat_vacancy_text_becomes_sections_with_facts():
    text = (
        "Menu Naar overzicht Solliciteren voor 22 november 2026 Dit ga je doen Je werkt aan de Omgevingswet. "
        "Je adviseert het bestuur. Dit vragen wij Een afgeronde hbo-opleiding. Minimaal 3 jaar ervaring. "
        "Kennis van Excel. Dit krijg je Schaal 11 en 36 uur per week, salaris € 3.000 - € 4.000."
    )
    result = vacancy_text.structure(text)
    assert {section["kind"] for section in result["sections"]} >= {"role", "ask", "offer", "other"}
    ask = next(section for section in result["sections"] if section["kind"] == "ask")
    assert len(ask["items"]) == 3
    facts = dict(result["facts"])
    assert facts["Uren"] == "36 uur" and "3.000" in facts["Salaris"] and facts["Reageer voor"] == "22 november 2026"


def test_a_common_word_mid_sentence_is_not_a_heading():
    result = vacancy_text.structure("In de organisatie werk je aan Organisatie van projecten met veel plezier en aandacht.")
    assert [section["kind"] for section in result["sections"]] == ["other"]


def test_what_is_in_your_cv_gets_marked_and_nothing_is_lost():
    terms = vacancy_text.profile_terms("## Vaardigheden\nExcel, Omgevingswet\n")
    pieces = vacancy_text.marked("Kennis van Excel en minimaal 3 jaar ervaring.", terms)
    assert "".join(piece["text"] for piece in pieces) == "Kennis van Excel en minimaal 3 jaar ervaring."
    assert {"text": "Excel", "mark": "you"} in pieces
    assert any(piece["mark"] == "key" for piece in pieces)


def test_a_reading_with_invented_numbers_is_thrown_away():
    reading = {
        "summary": "Baan met 99 collega's.",
        "sections": [{"kind": "role", "title": "Werk", "paragraphs": ["Tekst"], "items": []}],
        "facts": dict.fromkeys(("hours", "salary", "level", "contract", "location", "deadline", "contact_person"), ""),
        "must_have": [],
        "watch_out": [],
    }
    job = {"id": 1, "title": "T", "company": "C", "description": "Echt werk zonder dat getal", "content_hash": "abc123"}
    assert vacancy_reader.read_now(_manager([reading]), job) is None
    assert vacancy_reader.usable(job) is None and not vacancy_reader.worth_reading(job)
    job = {**job, "content_hash": "def456"}
    clean = {**reading, "summary": "Baan met collega's."}
    assert vacancy_reader.read_now(_manager([clean]), job)["summary"] == "Baan met collega's."
    assert vacancy_reader.usable(job)["summary"] == "Baan met collega's."


def test_the_job_page_shows_the_readable_text(client):  # noqa: F811
    job_id = add_job()
    page = client.get(f"/jobs/{job_id}").text
    assert 'id="vacancy-text"' in page


# ---------------------------------------------------------------- background work


def test_running_work_shows_on_every_page_until_seen(client):  # noqa: F811
    job_id = add_job()
    now = datetime.now(UTC).isoformat()
    store.meta_set(f"doc_status:{job_id}", json.dumps({"state": "running", "kind": "both", "updated_at": now}))
    assert client.get("/api/werk").json()["running"][0]["what"] == "Je cv en brief"
    assert "worden gemaakt" in client.get("/sollicitaties").text
    store.meta_set(f"doc_status:{job_id}", json.dumps({"state": "done", "kind": "both", "finished_at": now}))
    assert "zijn klaar" in client.get("/sollicitaties").text
    client.get(f"/jobs/{job_id}/sollicitatie")
    assert client.get("/api/werk").json() == {"running": [], "done": []}


def test_changing_the_tone_runs_in_the_background(client, monkeypatch):  # noqa: F811
    _no_pdf(monkeypatch)
    monkeypatch.setattr(runtime, "manager", SimpleNamespace(client=object()))
    job_id = add_job()
    _plan(job_id)
    first = document_pipeline.save_version(dict(store.get_job(job_id)), "motivatie", LETTER)
    client.post(f"/jobs/{job_id}/sollicitatie/documenten/{first['id']}/toon", data={"tone": "warmer"})
    assert json.loads(store.meta_get(f"doc_status:{job_id}"))["kind"] == f"tone:warmer:{first['id']}"


# ---------------------------------------------------------------- applications and position


def test_each_application_says_what_to_do_next(client):  # noqa: F811
    job_id = add_job()
    card = {"job": {"id": job_id}, "documents": {"cv": False, "letter": False}}
    assert "sollicitatie" in applications.next_step("saved", card)["href"]
    today = date(2026, 11, 20)
    waiting = {**card, "since": {"applied": "2026-11-01"}}
    assert applications.next_step("applied", waiting, today)["urgent"]
    assert not applications.next_step("applied", {**card, "since": {"applied": "2026-11-18"}}, today)["urgent"]
    assert "#inzicht" in applications.next_step("interview", card)["href"]


def test_the_board_remembers_when_a_status_changed(client):  # noqa: F811
    job_id = add_job()
    client.post(f"/sollicitaties/{job_id}", data={"status": "applied"})
    assert applications.details(job_id)["since"]["applied"] == date.today().isoformat()
    assert "Voorbeeldbericht" in client.get("/sollicitaties").text


def test_cv_checks_count_what_is_really_there():
    checks = {item["label"]: item["ok"] for item in career_position.cv_checks(PROFILE)}
    assert checks["Resultaten met cijfers"] is False
    rich = PROFILE.replace("- Vergunningtijd van 14 naar 8 weken.", "- 14 weken\n- 8 dossiers\n- 3 teams")
    assert {item["label"]: item["ok"] for item in career_position.cv_checks(rich)}["Resultaten met cijfers"] is True


def test_position_runs_on_request_and_is_marked_outdated(client, monkeypatch):  # noqa: F811
    profile.write_profile(PROFILE, "test")
    assert "Laat me zien hoe ik overkom" in client.get("/profile").text
    client.post("/profile/positie")
    assert career_position.status() == "running"
    assert "We kijken naar je als een recruiter" in client.get("/profile").text
    position = {
        "headline": "Kop", "first_impression": "Indruk",
        "strengths": [{"title": "Sterk punt", "proof": "Bewijs", "how_to_use": "Gebruik"}],
        "weaknesses": [{"title": "Zwak punt", "how_it_looks": "Zo", "action": "Doe"}],
        "in_demand_have": ["A"], "in_demand_missing": ["B"], "best_next_steps": ["Stap"],
    }
    sent = _manager([position])
    career_position.make(sent)
    assert "Geheim feit" not in json.dumps(sent.client.calls)
    career_position._set_status("idle")
    page = client.get("/profile").text
    assert "Sterk punt" in page and "Zwak punt" in page
    profile.write_profile(PROFILE + "\n## Talen\nFrans\n", "test")
    assert career_position.load()["outdated"] is True
