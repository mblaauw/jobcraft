"""LLM facade: direct OpenAI-compatible calls plus the legacy test fake path."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import suppress

from . import config, interview_state, profile, prompts, store, tenancy
from .llm_client import (
    Completion,
    FakeLLMClient,
    LLMAuthError,
    LLMError,
    LLMRateLimited,
    LLMSchemaError,
    OpenAICompatClient,
)

log = logging.getLogger("jobcraft.llm")


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Reduce provider message shapes to {id, role, text, model, error}."""
    out: list[dict] = []
    for message in messages:
        kind = message.get("type")
        if kind == "user":
            out.append(
                {
                    "id": message.get("id"),
                    "role": "user",
                    "text": message.get("text", ""),
                    "model": None,
                    "error": None,
                }
            )
        elif kind == "assistant":
            parts = message.get("content") or []
            text = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and part.get("type") == "text"
            )
            if not text.strip() and not message.get("error"):
                continue
            out.append(
                {
                    "id": message.get("id"),
                    "role": "assistant",
                    "text": text,
                    "model": message.get("model"),
                    "error": message.get("error"),
                }
            )
    return out


def _usage_delta(before: dict, after: dict) -> dict:
    def tokens(info: dict) -> dict:
        value = info.get("tokens") or {}
        cache = value.get("cache") or {}
        return {
            "input_tokens": int(value.get("input") or 0),
            "output_tokens": int(value.get("output") or 0),
            "cache_read": int(cache.get("read") or 0),
            "cache_write": int(cache.get("write") or 0),
            "cost_usd": float(info.get("cost") or 0.0),
        }

    now = tokens(after)
    old = tokens(before)
    return {key: now[key] - old[key] for key in now}


class ServerManager:
    """Compatibility facade; production uses the direct HTTP client."""

    def __init__(self) -> None:
        self.client = None
        self.proc = None
        self.password = None

    @property
    def base_url(self) -> str:
        return config.LLM_BASE_URL

    def start(self) -> None:
        if not config.LLM_BASE_URL or not config.LLM_API_KEY:
            log.warning("LLM_BASE_URL or LLM_API_KEY is missing; LLM features disabled")
            self.client = None
            return
        self.client = OpenAICompatClient(
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            model=config.LLM_MODEL,
            timeout=config.LLM_TIMEOUT,
            max_retries=config.LLM_MAX_RETRIES,
            structured=config.LLM_STRUCTURED,
        )

    def ensure_credential(self) -> None:
        return None

    def stop(self) -> None:
        if self.client is not None and hasattr(self.client, "close"):
            self.client.close()
        self.client = None

    def create_session(self, *, agent: str | None = None, model: bool = True) -> str:
        return uuid.uuid4().hex

    def _user_message(self, text: str) -> dict:
        return {
            "id": uuid.uuid4().hex,
            "role": "user",
            "text": text,
            "model": None,
            "error": None,
        }

    def run_turn(self, session_id: str, text: str) -> dict:
        if self.client is None:
            raise LLMError("LLM client is not configured")
        if not hasattr(self.client, "complete"):
            return self._legacy_run_turn(session_id, text)
        open_topics = interview_state.open_topics()
        topics_line = (
            "Nog open onderwerpen: " + "; ".join(open_topics)
            if open_topics
            else "Alle interviewonderwerpen zijn besproken."
        )
        context = (
            "[ACCOUNT_CONTEXT]\nUse only this user's supplied context, never files or other sessions.\n"
            f"Name: {tenancy.display_name()}\nPublic profile:\n{profile.read_public_excerpt(16000)}\n"
            f"{topics_line}\n[/ACCOUNT_CONTEXT]"
        )
        system = prompts.load("interviewer") or "You are a career interviewer."
        history = [
            {"role": row["role"], "content": row["text"]}
            for row in store.list_messages(session_id)
            if row["role"] in ("user", "assistant") and not row["hidden"]
        ]
        budget = int(os.environ.get("INTERVIEW_HISTORY_CHARS", "60000"))
        while len(history) > 2 and sum(len(item["content"]) for item in history) > budget:
            history.pop(0)
        messages = [{"role": "system", "content": system + "\n\n" + context}]
        messages.extend(history)
        messages.append({"role": "user", "content": text})
        completion = self.client.complete(
            messages, model=config.role_model("interviewer"), session_key=session_id
        )
        assistant = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "text": completion.text,
            "model": completion.model,
            "error": None,
        }
        return {
            "messages": [self._user_message(text), assistant],
            "usage": completion.usage,
            "session": {},
        }

    def run_turn_stream(self, session_id: str, text: str) -> Iterator[dict[str, object]]:
        if self.client is None:
            raise LLMError("LLM client is not configured")
        open_topics = interview_state.open_topics()
        topics_line = (
            "Nog open onderwerpen: " + "; ".join(open_topics)
            if open_topics
            else "Alle interviewonderwerpen zijn besproken."
        )
        context = (
            "[ACCOUNT_CONTEXT]\nUse only this user's supplied context, never files or other sessions.\n"
            f"Name: {tenancy.display_name()}\nPublic profile:\n{profile.read_public_excerpt(16000)}\n"
            f"{topics_line}\n[/ACCOUNT_CONTEXT]"
        )
        system = prompts.load("interviewer") or "You are a career interviewer."
        history = [
            {"role": row["role"], "content": row["text"]}
            for row in store.list_messages(session_id)
            if row["role"] in ("user", "assistant") and not row["hidden"]
        ]
        budget = int(os.environ.get("INTERVIEW_HISTORY_CHARS", "60000"))
        while len(history) > 2 and sum(len(item["content"]) for item in history) > budget:
            history.pop(0)
        messages = [{"role": "system", "content": system + "\n\n" + context}]
        messages.extend(history)
        messages.append({"role": "user", "content": text})
        user_message = self._user_message(text)

        def complete_without_stream() -> tuple[dict, dict]:
            result = self.run_turn(session_id, text)
            assistant = next(
                message
                for message in reversed(result["messages"])
                if message["role"] == "assistant"
            )
            return assistant, result.get("usage") or {}

        def streamed_done(assistant: dict, usage: dict) -> dict:
            return {
                "type": "done",
                "assistant": assistant,
                "user": user_message,
                "usage": usage,
            }

        if not hasattr(self.client, "stream_complete"):
            assistant, fallback_usage = complete_without_stream()
            yield {"type": "token", "text": assistant["text"]}
            yield streamed_done(assistant, fallback_usage)
            return
        assistant_id = uuid.uuid4().hex
        model = config.role_model("interviewer")
        full = ""
        usage: dict[str, object] = {}
        try:
            for event in self.client.stream_complete(messages, model=model, session_key=session_id):
                if event.get("type") == "token":
                    token = str(event.get("text") or "")
                    if token:
                        full += token
                        yield {"type": "token", "text": token}
                elif event.get("type") == "usage":
                    usage = event.get("usage") or {}
        except LLMError as exc:
            if full:
                yield {"type": "error", "detail": str(exc)}
                return
            # Streaming failed before any text; fall back to the non-streaming call.
            assistant, fallback_usage = complete_without_stream()
            yield {"type": "token", "text": assistant["text"]}
            yield streamed_done(assistant, fallback_usage)
            return
        if not full:
            # A provider that ignores stream=true returns no deltas; use the
            # regular completion path instead of persisting an empty reply.
            assistant, fallback_usage = complete_without_stream()
            yield {"type": "token", "text": assistant["text"]}
            yield streamed_done(assistant, fallback_usage)
            return
        assistant = {
            "id": assistant_id,
            "role": "assistant",
            "text": full,
            "model": model,
            "error": None,
        }
        yield streamed_done(assistant, usage)

    def generate(
        self, prompt: str, *, agent: str, timeout_note: str = "", schema: dict | None = None
    ) -> tuple[str, dict]:
        if self.client is None:
            raise LLMError("LLM client is not configured")
        if not hasattr(self.client, "complete"):
            return self._legacy_generate(prompt, agent=agent, timeout_note=timeout_note)
        system = prompts.load(agent)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        completion = self.client.complete(
            messages,
            model=config.role_model(agent),
            schema=schema,
            session_key=f"{agent}-{uuid.uuid4().hex}",
        )
        return completion.text, completion.usage

    # -- test-only compatibility with the old fake client -----------------
    def _legacy_run_turn(self, session_id: str, text: str) -> dict:
        before = self.client.session_info(session_id)
        open_topics = interview_state.open_topics()
        topics_line = (
            "Nog open onderwerpen: " + "; ".join(open_topics)
            if open_topics
            else "Alle interviewonderwerpen zijn besproken."
        )
        context = (
            "[ACCOUNT_CONTEXT]\nUse only this user's supplied context, never files or other sessions.\n"
            f"Name: {tenancy.display_name()}\nPublic profile:\n{profile.read_public_excerpt(16000)}\n"
            f"{topics_line}\n"
            "[/ACCOUNT_CONTEXT]\n[USER_MESSAGE]\n"
        )
        digest = hashlib.sha256(context.encode()).hexdigest()
        session = store.get_session(session_id)
        fresh_context = session is None or session["context_digest"] != digest
        self.client.prompt(session_id, context + text if fresh_context else text)
        if session is not None:
            store.update_session(session_id, context_digest=digest)
        self.client.wait(session_id)
        raw_messages = self.client.messages(session_id)
        raw_messages.reverse()
        normalized = normalize_messages(raw_messages)
        for message in normalized:
            if message["role"] == "user" and message["text"].startswith("[ACCOUNT_CONTEXT]"):
                message["text"] = message["text"].split("[/ACCOUNT_CONTEXT]\n[USER_MESSAGE]\n", 1)[
                    -1
                ]
        assistant = [message for message in normalized if message["role"] == "assistant"]
        if not assistant:
            raise LLMError("the model returned no assistant reply")
        after = self.client.session_info(session_id)
        usage = _usage_delta(before, after)
        return {"messages": normalized, "usage": usage, "session": after}

    def _legacy_generate(
        self, prompt: str, *, agent: str, timeout_note: str = ""
    ) -> tuple[str, dict]:
        session_id = self.client.create_session(agent=agent)
        try:
            self.client.prompt(session_id, prompt)
            self.client.wait(session_id)
            raw_messages = self.client.messages(session_id)
            raw_messages.reverse()
            normalized = normalize_messages(raw_messages)
            assistant = [m for m in normalized if m["role"] == "assistant"]
            info = self.client.session_info(session_id)
            usage = _usage_delta({}, info)
            if not assistant:
                raise LLMError("the model returned no assistant message")
            return assistant[-1]["text"].strip(), usage
        finally:
            with suppress(LLMError):
                self.client.delete_session(session_id)


__all__ = [
    "Completion",
    "FakeLLMClient",
    "LLMAuthError",
    "LLMError",
    "LLMRateLimited",
    "LLMSchemaError",
    "OpenAICompatClient",
    "ServerManager",
    "normalize_messages",
]
