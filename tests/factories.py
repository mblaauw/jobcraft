"""Reusable synthetic fixtures; never backed by real personal data."""

from __future__ import annotations

from collections import deque
from typing import Any

from app import store


def add_job(
    i: int = 1,
    *,
    location: str = "Amsterdam",
    posted_at: str = "2026-10-01T10:00:00",
    description: str = "Synthetic public vacancy text",
    lat: float | None = None,
    lon: float | None = None,
) -> int:
    job_id, _ = store.upsert_job(
        {
            "source_id": "fixture",
            "url": f"https://example.test/jobs/{i}",
            "title": f"Fixture vacancy {i}",
            "company": "Fixture employer",
            "location": location,
            "hours": "24 uur",
            "description": description,
            "posted_at": posted_at,
            "content_hash": f"fixture-{i}",
            "lat": lat,
            "lon": lon,
        }
    )
    return job_id


class FakeLLM:
    """Queue canned (text, usage) replies and record every prompt."""

    def __init__(self, replies: list[tuple[str, dict[str, Any]]] | None = None):
        self.replies = deque(replies or [("{}", {})])
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, agent: str = "", **kwargs):
        self.prompts.append(prompt)
        if not self.replies:
            raise RuntimeError("FakeLLM has no queued reply")
        return self.replies.popleft()

    def run_turn(self, session_id: str, text: str) -> dict:
        self.prompts.append(text)
        message = {
            "id": "assistant-fixture",
            "role": "assistant",
            "text": "Fixture reply",
        }
        return {"messages": [message], "usage": {}, "session": {}}


def fake_decide(answers: dict | None = None):
    calls: list[dict] = []
    payload = answers or {"fit": {"type": "score", "score": 3, "confidence": 0.9}}

    def decide(state: str, *args, **kwargs):
        calls.append({"state": state})
        return {"answers": payload}, {}, 1

    decide.calls = calls  # type: ignore[attr-defined]
    return decide
