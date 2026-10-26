from __future__ import annotations

import httpx
import pytest

from app.llm_client import LLMAuthError, LLMError, OpenAICompatClient


def _client(handler, *, retries=0):
    client = OpenAICompatClient(
        base_url="https://llm.example/v1",
        api_key="test-key",
        model="fixture-model",
        max_retries=retries,
    )
    client._client = httpx.Client(
        base_url="https://llm.example/v1",
        transport=httpx.MockTransport(handler),
        headers={"authorization": "Bearer test-key", "user-agent": "jobcraft/test"},
    )
    return client


def test_complete_sends_model_messages_and_reads_usage():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.read().decode()
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "model": "fixture-model",
                "choices": [{"message": {"content": "hallo"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    completion = _client(handler).complete([{"role": "user", "content": "Hoi"}])
    assert completion.text == "hallo"
    assert completion.usage["input_tokens"] == 12
    assert completion.usage["output_tokens"] == 3
    assert '"model"' in seen["body"] and "fixture-model" in seen["body"]
    assert seen["auth"] == "Bearer test-key"


def test_complete_retries_rate_limit_then_succeeds():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {}},
        )

    completion = _client(handler, retries=1).complete([{"role": "user", "content": "Hoi"}])
    assert completion.text == "ok"
    assert len(calls) == 2


def test_auth_error_is_typed():
    def handler(request: httpx.Request):
        return httpx.Response(401, json={"error": "nope"})

    with pytest.raises(LLMAuthError):
        _client(handler).complete([{"role": "user", "content": "Hoi"}])


def test_server_error_raises_llm_error():
    def handler(request: httpx.Request):
        return httpx.Response(500)

    with pytest.raises(LLMError):
        _client(handler, retries=0).complete([{"role": "user", "content": "Hoi"}])


def test_stream_complete_yields_tokens_and_usage():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.read().decode()
        body = (
            'data: {"choices":[{"delta":{"content":"Hal"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            'data: {"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body.encode())

    events = list(_client(handler).stream_complete([{"role": "user", "content": "Hoi"}]))
    assert [event["text"] for event in events if event["type"] == "token"] == ["Hal", "lo"]
    usage = next(event["usage"] for event in events if event["type"] == "usage")
    assert usage["input_tokens"] == 5
    assert usage["output_tokens"] == 2
    assert '"stream":true' in seen["body"]
    assert "include_usage" in seen["body"]
