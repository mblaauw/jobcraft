"""Minimal OpenAI-compatible Chat Completions client (no SDK dependency)."""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("jobcraft.llm")

# How hard a model may think before it answers. Reasoning models think by default, which on a
# short JSON verdict is most of the wait: measured on the matcher, 80% of its output tokens.
REASONING_EFFORTS = ("low", "medium", "high")
REASONING_OFF = "off"


def reasoning_body(reasoning: str | None) -> dict[str, Any]:
    """The request fields for a reasoning setting; nothing for the default or an unknown value.

    Effort is the OpenAI-style field (gpt, Anthropic and most gateways). "off" is the
    DeepSeek-style switch; Anthropic models refuse it, so it is for DeepSeek only."""
    if reasoning in REASONING_EFFORTS:
        return {"reasoning_effort": reasoning}
    if reasoning == REASONING_OFF:
        return {"thinking": {"type": "disabled"}}
    return {}


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
    def _session_headers(self, session_key: str | None) -> dict:
        # Only the OpenCode gateway uses this; other providers need not see session keys.
        return {"x-opencode-session": session_key} if session_key and "opencode" in self.base_url else {}

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
        # (model, reasoning) pairs a model refused: sent without the setting from then on.
        self._refused: set[tuple[str, str]] = set()
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

    def stream_complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        session_key: str | None = None,
        reasoning: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if (body["model"], reasoning) not in self._refused:
            body.update(reasoning_body(reasoning))
        headers = self._session_headers(session_key)
        with self._client.stream(
            "POST", "/chat/completions", json=body, headers=headers
        ) as response:
            if response.status_code in (401, 403):
                raise LLMAuthError("LLM-provider weigert de API-key")
            if response.status_code == 429:
                raise LLMRateLimited("LLM-provider rate limit")
            if response.status_code >= 400:
                raise LLMError(f"LLM-provider HTTP {response.status_code}")
            for line in response.iter_lines():
                if not line:
                    continue
                text = line.strip()
                if not text.startswith("data:"):
                    continue
                payload_text = text[5:].strip()
                if payload_text == "[DONE]":
                    break
                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                if payload.get("usage"):
                    yield {"type": "usage", "usage": _usage(payload, body["model"])}
                choices = payload.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield {"type": "token", "text": delta}

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        schema: dict | None = None,
        session_key: str | None = None,
        reasoning: str | None = None,
    ) -> Completion:
        extra = {} if (model or self.model, reasoning) in self._refused else reasoning_body(reasoning)
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            **extra,
        }
        if schema and self.structured != "none":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        headers = self._session_headers(session_key)
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
                elif response.status_code == 400 and extra:
                    # A model that does not take this reasoning setting answers without it,
                    # rather than failing someone's verdict or letter over a tuning knob.
                    log.warning("model %s refused reasoning=%s; asking again without it", body["model"], reasoning)
                    completion = self.complete(messages, model=model, schema=schema, session_key=session_key)
                    self._refused.add((body["model"], str(reasoning)))  # it was the setting: skip it next time
                    return completion
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

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        schema: dict | None = None,
        session_key: str | None = None,
        reasoning: str | None = None,
    ) -> Completion:
        self.calls.append(
            {"messages": messages, "model": model, "schema": schema, "session_key": session_key, "reasoning": reasoning}
        )
        if not self.replies:
            raise LLMError("FakeLLMClient has no queued reply")
        text = self.replies.pop(0)
        return Completion(text=text, usage={}, model=model or "fake")

    def stream_complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        session_key: str | None = None,
        reasoning: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(
            {"messages": messages, "model": model, "schema": None, "session_key": session_key, "reasoning": reasoning}
        )
        if not self.replies:
            raise LLMError("FakeLLMClient has no queued reply")
        text = self.replies.pop(0)
        # Yield in small chunks so streaming UI can be exercised.
        for i in range(0, len(text), 12):
            yield {"type": "token", "text": text[i : i + 12]}
        yield {"type": "usage", "usage": {}}
