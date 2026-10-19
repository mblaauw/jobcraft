from __future__ import annotations

from app.llm import normalize_messages


def test_normalizes_user_and_assistant_shapes():
    raw = [
        {
            "id": "msg_1",
            "type": "user",
            "text": "Hallo",
        },
        {
            "id": "msg_2",
            "type": "assistant",
            "agent": "interviewer",
            "model": {"id": "deepseek-v4.1-flash", "providerID": "fixture-provider"},
            "content": [
                {"type": "text", "text": "Welke "},
                {"type": "text", "text": "werkdag past?"},
            ],
            "finish": "stop",
        },
        {"id": "msg_3", "type": "idle", "outcome": "succeeded"},
        {"id": "msg_4", "type": "agent-switched", "agent": "interviewer"},
    ]
    normalized = normalize_messages(raw)
    assert [item["role"] for item in normalized] == ["user", "assistant"]
    assert normalized[0]["text"] == "Hallo"
    assert normalized[1]["text"] == "Welke werkdag past?"
    assert normalized[1]["model"]["providerID"] == "fixture-provider"


def test_assistant_error_is_surfaced():
    raw = [
        {
            "id": "msg_1",
            "type": "assistant",
            "content": [],
            "error": {"type": "provider.auth", "message": "nope"},
        }
    ]
    normalized = normalize_messages(raw)
    assert normalized[0]["error"]["type"] == "provider.auth"
    assert normalized[0]["text"] == ""
