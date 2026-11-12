"""How hard a task's model may think: chosen per task, sent only when chosen, never fatal."""

from __future__ import annotations

import json

import httpx
import pytest
from test_auth import csrf_of
from test_auth_events import _bootstrap_password, _full_app_client
from test_jobs_page import client  # noqa: F401
from test_llm_client import _client

from app import llm, model_routes, store, tenancy
from app.llm_client import FakeLLMClient, LLMError, reasoning_body

OK = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def test_each_setting_becomes_the_field_the_gateway_takes():
    assert reasoning_body("low") == {"reasoning_effort": "low"}
    assert reasoning_body("high") == {"reasoning_effort": "high"}
    assert reasoning_body("off") == {"thinking": {"type": "disabled"}}
    assert reasoning_body(None) == reasoning_body("") == reasoning_body("minimal") == {}


def test_the_request_carries_the_setting_only_when_one_is_chosen():
    bodies = []

    def handler(request: httpx.Request):
        bodies.append(json.loads(request.read()))
        return httpx.Response(200, json=OK)

    llm_client = _client(handler)
    llm_client.complete([{"role": "user", "content": "Hoi"}])
    llm_client.complete([{"role": "user", "content": "Hoi"}], reasoning="low")
    assert "reasoning_effort" not in bodies[0] and "thinking" not in bodies[0]
    assert bodies[1]["reasoning_effort"] == "low"


def test_a_model_that_refuses_the_setting_answers_without_it():
    bodies = []

    def handler(request: httpx.Request):
        body = json.loads(request.read())
        bodies.append(body)
        if "thinking" in body:
            return httpx.Response(400, json={"error": {"message": "thinking: cannot be represented"}})
        return httpx.Response(200, json=OK)

    llm_client = _client(handler)
    completion = llm_client.complete([{"role": "user", "content": "Hoi"}], reasoning="off")
    assert completion.text == "ok"
    assert len(bodies) == 2 and "thinking" not in bodies[1]
    llm_client.complete([{"role": "user", "content": "Hoi"}], reasoning="off")
    assert len(bodies) == 3 and "thinking" not in bodies[2]  # remembered: no second refusal per call


def test_a_refusal_for_another_reason_still_fails_and_blames_nothing():
    bodies = []

    def handler(request: httpx.Request):
        bodies.append(json.loads(request.read()))
        return httpx.Response(400, json={"error": {"message": "bad schema"}})

    llm_client = _client(handler)
    with pytest.raises(LLMError):
        llm_client.complete([{"role": "user", "content": "Hoi"}], reasoning="low")
    assert len(bodies) == 2 and not llm_client._refused  # the setting was not the cause


def test_the_interview_stream_takes_the_setting_and_falls_back_when_refused(client, monkeypatch):  # noqa: F811
    model_routes.save_reasoning({"interviewer": "off"})
    bodies = []

    def handler(request: httpx.Request):
        body = json.loads(request.read())
        bodies.append(body)
        if "thinking" in body:
            return httpx.Response(400, json={"error": {"message": "thinking: cannot be represented"}})
        return httpx.Response(200, json={**OK, "choices": [{"message": {"content": "Vertel eens."}}]})

    manager = llm.ServerManager()
    manager.client = _client(handler)
    store.create_session("gesprek-1", "Gesprek", "interviewer", "fixture-model")
    events = list(manager.run_turn_stream("gesprek-1", "Hallo"))
    assert bodies[0]["stream"] and bodies[0]["thinking"] == {"type": "disabled"}
    assert events[-1]["type"] == "done" and events[-1]["assistant"]["text"] == "Vertel eens."
    assert all("thinking" not in body for body in bodies[2:])


def test_the_environment_setting_reaches_the_request_when_the_admin_chose_nothing(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LLM_REASONING_MATCHER", "low")
    manager = llm.ServerManager()
    manager.client = FakeLLMClient(['{"score": 70}'])
    manager.generate("Beoordeel", agent="matcher")
    assert manager.client.calls[0]["reasoning"] == "low"
    admin = _full_app_client("admin", _bootstrap_password("admin"))
    assert "Standaard (kort)" in admin.get("/admin/ai").text


def test_the_admin_choice_wins_then_the_environment_then_nothing(client, monkeypatch):  # noqa: F811
    assert model_routes.reasoning_for("matcher") == ""
    monkeypatch.setenv("LLM_REASONING_MATCHER", "Low")
    assert model_routes.reasoning_for("matcher") == "low"
    model_routes.save_reasoning({"matcher": "off", "cv_writer": ""})
    assert model_routes.reasoning_for("matcher") == "off"
    assert model_routes.reasoning_for("cv_writer") == ""
    monkeypatch.setenv("LLM_REASONING_CV_WRITER", "maximal")  # not offered: ignored, not sent
    assert model_routes.reasoning_for("cv_writer") == ""
    with pytest.raises(ValueError):
        model_routes.save_reasoning({"matcher": "maximal"})
    with tenancy.scope({"id": "someone-else", "username": "x", "display_name": "x", "role": "user"}):
        assert model_routes.reasoning_for("matcher") == "off"  # one choice for the whole installation


def test_every_call_for_a_task_takes_its_setting(client):  # noqa: F811
    model_routes.save_reasoning({"matcher": "low"})
    manager = llm.ServerManager()
    manager.client = FakeLLMClient(['{"score": 70}', '{"ok": true}'])
    manager.generate("Beoordeel", agent="matcher")
    manager.generate("Schrijf", agent="cv_writer")
    assert [call["reasoning"] for call in manager.client.calls] == ["low", None]


def test_the_ai_page_saves_the_setting_next_to_the_model(client):  # noqa: F811
    admin = _full_app_client("admin", _bootstrap_password("admin"))
    page = admin.get("/admin/ai").text
    assert page.count('name="reasoning_') == len(model_routes.ROLES)
    form = {"_csrf": csrf_of(page), "reasoning_matcher": "low", "model_matcher": "gpt-6-luna"}
    saved = admin.post("/admin/ai/modellen", data=form, follow_redirects=False)
    assert saved.status_code == 303 and "melding" in saved.headers["location"]
    assert model_routes.reasoning_for("matcher") == "low" and model_routes.model_for("matcher") == "gpt-6-luna"
    assert '<option value="low" selected>' in admin.get("/admin/ai").text
    refused = admin.post(
        "/admin/ai/modellen",
        data={**form, "model_matcher": "ander-model", "reasoning_matcher": "maximal"},
        follow_redirects=False,
    )
    assert "fout" in refused.headers["location"]
    assert model_routes.model_for("matcher") == "gpt-6-luna"  # nothing half-saved


def test_the_ai_page_test_button_sends_a_session_so_the_gateway_answers(client, monkeypatch):  # noqa: F811
    manager = llm.ServerManager()
    manager.client = FakeLLMClient(["OK"])
    monkeypatch.setattr("app.routers.admin_ai.runtime.get_manager", lambda: manager)
    admin = _full_app_client("admin", _bootstrap_password("admin"))
    page = admin.get("/admin/ai").text
    answer = admin.post("/admin/ai/test", data={"_csrf": csrf_of(page), "role": "matcher"}, follow_redirects=False)
    assert "werkt" in answer.headers["location"] and "niet" not in answer.headers["location"]
    assert manager.client.calls[0]["session_key"].startswith("matcher-")
