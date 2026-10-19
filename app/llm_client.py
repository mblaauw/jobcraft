"""Minimal OpenAI-compatible Chat Completions client (no SDK dependency)."""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


class LLMAuthError(LLMError):
    pass


class LLMRateLimited(LLMError):
    pass


class LLMSchemaError(LLMError):
    def __init__(self, message: str, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage or {}


@dataclass
class Completion:
    text: str
    usage: dict[str, Any]
    model: str
    parsed: Any | None = None


def _usage(payload: dict, model: str) -> dict[str, Any]:
    raw = payload.get("usage") or {}
    input_tokens = int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0)
    output_tokens = int(raw.get("completion_tokens") or raw.get("output_tokens") or 0)
    cost = raw.get("cost_usd")
    if cost is None:
        slug = model.replace("/", "-").replace(".", "-").upper()
        try:
            price_in = float(os.environ.get(f"LLM_PRICE_{slug}_IN", "0") or 0)
            price_out = float(os.environ.get(f"LLM_PRICE_{slug}_OUT", "0") or 0)
        except ValueError:
            price_in = price_out = 0.0
        cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": float(cost or 0.0),
    }


class OpenAICompatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 2,
        structured: str = "json_schema",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.structured = structured
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "authorization": f"Bearer {api_key}",
                "user-agent": f"jobcraft/{os.environ.get('JOBCRAFT_VERSION', '0.1.0')}",
            },
        )

    def close(self) -> None:
        self._client.close()

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        schema: dict | None = None,
        session_key: str | None = None,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
        }
        if schema and self.structured != "none":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        headers = {"x-opencode-session": session_key} if session_key else {}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post("/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code in (401, 403):
                    raise LLMAuthError("LLM-provider weigert de API-key") from None
                if response.status_code == 429:
                    last_error = LLMRateLimited("LLM-provider rate limit")
                    if attempt < self.max_retries:
                        delay = float(response.headers.get("retry-after", 2**attempt))
                        time.sleep(delay + random.uniform(0, 0.25))
                        continue
                    raise last_error
                if response.status_code >= 500:
                    last_error = LLMError(f"LLM-provider HTTP {response.status_code}")
                elif response.status_code >= 400:
                    raise LLMError(f"LLM-provider HTTP {response.status_code}")
                else:
                    payload = response.json()
                    choices = payload.get("choices") or []
                    if not choices:
                        raise LLMError("LLM response has no choices")
                    text = (choices[0].get("message") or {}).get("content") or ""
                    return Completion(
                        text=text,
                        usage=_usage(payload, body["model"]),
                        model=body["model"],
                    )
            if attempt < self.max_retries:
                time.sleep((2**attempt) + random.uniform(0, 0.25))
        raise LLMError(str(last_error or "LLM request failed"))


class FakeLLMClient:
    """Test client: queued replies, recorded calls, no network."""

    def __init__(self, replies: list[str] | None = None):
        self.replies = list(replies or ['{"ok": true}'])
        self.calls: list[dict] = []

    def close(self) -> None:
        pass

    def complete(self, messages, *, model=None, schema=None, session_key=None):
        self.calls.append(
            {"messages": messages, "model": model, "schema": schema, "session_key": session_key}
        )
        if not self.replies:
            raise LLMError("FakeLLMClient has no queued reply")
        text = self.replies.pop(0)
        return Completion(text=text, usage={}, model=model or "fake")
