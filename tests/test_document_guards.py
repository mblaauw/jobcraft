"""A CV or letter says nothing that was not given, and a failure halfway is said plainly."""

from __future__ import annotations

import json

import pytest

from app import (
    cv_ai,
    document_pipeline,
    employer_research,
    interview_state,
    profile,
    store,
    workflows,
)
from app.document_content import letter_length_problem, style_problems, unsupported_facts
from tests.test_jobs_page import add_job, client  # noqa: F401


def test_an_invented_name_does_not_pass_as_letters_inside_another_word():
    source = "Werk bij channel in rooster, januari. Zorggroep Amstelland's team in de Amsterdamse zorg. Sam Jansen."
    problems = unsupported_facts(["Bij Ann leerde ik veel over Roos en Jan."], source)
    assert [name for name in ("Ann", "Roos", "Jan") if any(f'"{name}"' in line for line in problems)] == ["Ann", "Roos", "Jan"]
    assert unsupported_facts(["Ik werkte in Amsterdam bij Zorggroep Amstelland met Sam."], source) == []
    # A longer name may open a compound word of the source.
    assert unsupported_facts(["Ik werk met Excel, Scrum en Python."], "excelsheets, scrummaster en Python3") == []
    # But never of a name: "Marie" is not known because the source mentions "Mariette".
    assert len(unsupported_facts(["Samen met Marie."], "Ik werkte met Mariette aan excelsheets.")) == 1


def test_style_checks_read_words_not_letters():
    assert style_problems(["Zij is compassievol en werkte 2019 – 2021 bij de gemeente."]) == []
    assert style_problems(["Mijn passie is groen."]) == ['Cliché: "passie".']
    assert style_problems(["Het werk – en dat meen ik – is mooi."])


@pytest.mark.parametrize(("words", "problem"), [(130, True), (160, False), (270, False), (300, True)])
def test_a_letter_far_from_the_standard_length_is_sent_back(words, problem):
    assert bool(letter_length_problem({"paragraphs": ["woord " * words]})) is problem


def test_an_employer_with_a_legal_suffix_is_still_looked_up(monkeypatch):
    text = "Welkom bij Albert Heijn. " + "Wij zijn een supermarkt met winkels in heel Nederland. " * 6
    monkeypatch.setattr(employer_research, "_site_of", lambda job, company: ["https://www.ah.test/"])
    monkeypatch.setattr(employer_research, "_fetch", lambda _client, url, robots: f"<html><body><p>{text}</p></body></html>")
    monkeypatch.setattr(employer_research, "_about_links", lambda html, address: [])
    monkeypatch.setattr(employer_research, "_wikipedia", lambda _client, company: None)
    pages = employer_research.gather({"company": "Albert Heijn B.V."})
    assert [page["url"] for page in pages] == ["https://www.ah.test/"]


def test_one_failed_document_of_two_is_named_and_only_that_one_is_redone(client, monkeypatch):  # noqa: F811
    from app import application_plan, tenancy
    from app.routers import studio

    job_id = add_job("Hovenier")
    monkeypatch.setattr(application_plan, "load", lambda job_id: {"angle": "x"})

    def create(mgr, job, kind, emit=lambda stage: None):
        if kind == "motivatie":
            raise RuntimeError("the model answered nonsense")
        return {"version": 1}

    monkeypatch.setattr(document_pipeline, "create", create)
    with pytest.raises(document_pipeline.Partly) as caught:
        document_pipeline.create_all(None, {"id": job_id}, ("resume", "motivatie"))
    assert caught.value.done == ["resume"] and caught.value.failed == ["motivatie"]

    with store.connect() as con:
        owner = con.execute("SELECT id, username, display_name, role FROM users WHERE username='test'").fetchone()
    with tenancy.scope(dict(owner)):
        monkeypatch.setattr(workflows, "get_manager", lambda: None)
        workflows._run_document(job_id, "both")
        status = workflows._doc_status(job_id)
        assert status["state"] == "error" and status["retry"] == "motivatie"
        assert "cv is gemaakt" in status["error"] and "alleen nog je brief" in status["error"]
        monkeypatch.setattr(studio.config, "LLM_API_KEY", "set")
        assert studio._enqueue(job_id, "both") == ""
        assert json.loads(store.meta_get(f"doc_status:{job_id}"))["kind"] == "motivatie"


def test_version_numbers_are_handed_out_one_at_a_time():
    assert document_pipeline._save_lock(3, "resume") is document_pipeline._save_lock(3, "resume")
    assert document_pipeline._save_lock(3, "resume") is not document_pipeline._save_lock(3, "motivatie")


def test_a_failed_cv_reading_keeps_the_text_for_another_try(monkeypatch):
    store.meta_set(cv_ai.TEXT_KEY, json.dumps({"text": "Mijn cv", "source": "tekst"}))
    monkeypatch.setattr(cv_ai, "read", lambda mgr, text: (_ for _ in ()).throw(RuntimeError("no answer")))
    monkeypatch.setattr(cv_ai, "get_manager", lambda: None)
    monkeypatch.setattr(cv_ai, "_scoped", lambda task, work: work())
    with pytest.raises(RuntimeError):
        cv_ai._task_import({"kind": "cv_import", "owner": None})
    assert cv_ai.text_waiting()
    cv_ai._set_status("error", "import", error="Dit lukte niet. Probeer het opnieuw.")
    assert cv_ai.status()["retry"] is True


def test_the_coach_does_not_read_what_was_said_in_the_interview():
    profile.write_profile(
        f"# Profiel\n\n## Kern\nPlanner bij een hovenier.\n\n## {profile.ANSWERS_HEADING}\nWil minder administratie.\n",
        note="test",
    )
    sources = cv_ai._other_sources()
    assert "Planner bij een hovenier" in sources and "minder administratie" not in sources


def test_a_topic_reopened_by_hand_stays_open():
    topic = interview_state.TOPIC_SPECS[0]
    words = " ".join(topic["keywords"][:3])
    for _ in range(3):
        interview_state.record_answer("Vraag", words)
    interview_state.toggle(topic["id"], "open")
    state = interview_state.record_answer("Vraag", words)
    assert next(item for item in state["topics"] if item["id"] == topic["id"])["status"] == "open"
