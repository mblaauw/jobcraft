from __future__ import annotations

import pytest

from app import systemone
from tests.test_jobs_page import client  # noqa: F401


def test_questions_use_the_correct_criteria_shapes():
    # score.criteria must be an ordered array (the API returns 400 otherwise)
    assert isinstance(systemone.QUESTIONS["fit"]["criteria"], list)
    assert 2 <= len(systemone.QUESTIONS["fit"]["criteria"]) <= 10
    # choice.criteria is a map
    assert isinstance(systemone.QUESTIONS["category"]["criteria"], dict)
    # noul has no required criteria
    assert systemone.QUESTIONS["phone_sales_main"]["type"] == "noul"
    for question in systemone.QUESTIONS.values():
        assert question["instructions"]


def test_signals_from_answers_full_and_confident():
    answers = {
        "fit": {"type": "score", "score": 3.99, "confidence": 0.99},
        "category": {"type": "choice", "choice": "groen_buiten", "confidence": 1.0},
        "phone_sales_main": {"type": "noul", "noul": 0.02},
        "evening_weekend": {"type": "noul", "noul": 0.9},
        "outdoor_mobile": {"type": "noul", "noul": 0.97},
    }
    signals = systemone.signals_from_answers(answers)
    assert signals["fit"] == 99.8
    assert signals["category"] == "groen_buiten"
    assert signals["noul"]["phone_sales_main"] == 0.02
    assert signals["confidence"] == 0.99  # the fit's own certainty
    assert signals["facts_confidence"] == 0.9  # min(0.99, 1.0, 0.98, 0.9, 0.97)
    assert signals["escalate"] is False


def test_low_confidence_escalates():
    answers = {
        "fit": {"type": "score", "score": 2.0, "confidence": 0.6},
        "category": {"type": "choice", "choice": "overig", "confidence": 0.67},
        "phone_sales_main": {"type": "noul", "noul": 0.5},
    }
    signals = systemone.signals_from_answers(answers)
    assert signals["confidence"] == 0.6 and signals["facts_confidence"] == 0.5
    assert signals["escalate"] is True


def test_a_sure_fit_is_not_escalated_because_a_fact_is_unclear():
    answers = {
        "fit": {"type": "score", "score": 0.03, "confidence": 0.98},
        "company_car": {"type": "noul", "noul": 0.5},
        "guidance": {"type": "noul", "noul": 0.4},
    }
    signals = systemone.signals_from_answers(answers)
    assert signals["confidence"] == 0.98 and signals["escalate"] is False


def test_facts_alone_always_escalate():
    signals = systemone.signals_from_answers({"day_hours": {"type": "noul", "noul": 0.99}})
    assert signals["fit"] is None and signals["escalate"] is True


def test_compose_state_contains_job_facts():
    job = {
        "title": "Medewerker Groen",
        "company": "Gemeente Amsterdam",
        "location": "Amsterdam",
        "hours": "24-32 uur",
        "description": "Buiten werken. " * 1000,
    }
    state = systemone.compose_state(job, "profieltekst")
    assert "Medewerker Groen" in state
    assert "Gemeente Amsterdam" in state
    assert "profieltekst" in state
    assert len(state) < 8000


def test_signals_key_changes_with_content_or_profile():
    job = {"content_hash": "abc"}
    assert systemone.signals_key(job, "profiel-a") != systemone.signals_key(job, "profiel-b")
    other = {"content_hash": "def"}
    assert systemone.signals_key(job, "profiel-a") != systemone.signals_key(other, "profiel-a")


class _FakeResponse:
    status_code = 200
    text = "{}"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_decide_posts_the_expected_contract(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(
            {
                "answers": {
                    "fit": {"type": "score", "score": 3.0, "confidence": 0.9},
                    "category": {"type": "choice", "choice": "groen_buiten", "confidence": 0.95},
                },
                "usage": {"input_tokens": 500, "output_tokens": 90},
            }
        )

    monkeypatch.setenv("RUNWARE_API_KEY", "test-key")
    monkeypatch.setattr(systemone.httpx, "post", fake_post)
    data, usage, latency = systemone.decide("state")
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == systemone.model_id()
    assert captured["json"]["state"] == "state"
    assert "fit" in captured["json"]["questions"]
    assert usage["output_tokens"] == 90
    assert latency >= 0
    assert data["answers"]["category"]["choice"] == "groen_buiten"


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("RUNWARE_API_KEY", raising=False)
    assert systemone.enabled() is False
    with pytest.raises(systemone.SystemOneError):
        systemone.decide("state")


def test_good_or_excellent_is_a_sure_fit_and_the_middle_is_not():
    sure = {"type": "score", "score": 3.4, "confidence": 0.53,
            "probabilities": {"0": 0, "1": 0.02, "2": 0.05, "3": 0.4, "4": 0.53}}
    assert systemone.signals_from_answers({"fit": sure})["confidence"] == 0.93
    assert systemone.signals_from_answers({"fit": sure})["escalate"] is False
    torn = {"type": "score", "score": 2.0, "confidence": 0.5,
            "probabilities": {"0": 0.1, "1": 0.2, "2": 0.4, "3": 0.2, "4": 0.1}}
    assert systemone.signals_from_answers({"fit": torn})["escalate"] is True


def test_any_provider_key_works_and_opencode_is_preferred(monkeypatch):
    monkeypatch.setenv("RUNWARE_API_KEY", "runware")
    assert systemone.api_key() == "runware"
    monkeypatch.setenv("SYSTEMONE_API_KEY", "opencode")
    assert systemone.api_key() == "opencode" and systemone.enabled()


def test_jev_tokens_leave_room_for_writing(client):  # noqa: F811
    from app import store

    store.record_usage(None, "systemone", 1_800_000, 0, 0.07, agent="jev")
    store.record_usage(None, "documents", 1_000, 500, 0.0)
    assert store.daily_usage_tokens() == 1_500
