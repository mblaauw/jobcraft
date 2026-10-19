"""Supervises an isolated ``opencode serve`` instance and wraps its v2 HTTP API.

Safety notes:
- The server password is captured from the server's stdout and kept in memory;
  it is redacted in the log file.
- Credential endpoints return secrets in their response bodies. Those bodies are
  never logged, echoed or included in exceptions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from . import config, interview_state, profile, store, tenancy

_PASSWORD_RE = re.compile(r"server password (\S+)")
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


class LLMError(RuntimeError):
    """Controlled failure while talking to the local OpenCode server."""


def prepare_runtime_home() -> None:
    """Assemble the isolated OpenCode HOME from repo templates (idempotent)."""
    config.RUNTIME_HOME.mkdir(parents=True, exist_ok=True)
    config.RUNTIME_CONFIG.mkdir(parents=True, exist_ok=True)
    config.RUNTIME_AGENTS.mkdir(parents=True, exist_ok=True)
    (config.RUNTIME_HOME / ".local/share/opencode").mkdir(parents=True, exist_ok=True)
    config.RUNTIME_WORKSPACE.mkdir(parents=True, exist_ok=True)
    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    source = (config.AGENTS_SOURCE / "opencode.json").read_text()
    (config.RUNTIME_CONFIG / "opencode.json").write_text(source)

    for agent_file in sorted(config.AGENTS_SOURCE.glob("*.md")):
        body = agent_file.read_text().replace("{{PROFILE_DIR}}", str(config.PROFILE_DIR))
        (config.RUNTIME_AGENTS / agent_file.name).write_text(body)


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Reduce OpenCode message shapes to {id, role, text, model, error}."""
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
                continue  # intermediate tool/reasoning step, nothing to show
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


class OpenCodeClient:
    """Thin typed-ish client over the v2 session API."""

    def __init__(self, base_url: str, password: str) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            auth=("opencode", password),
            timeout=httpx.Timeout(600.0, connect=10.0),
        )

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        hide_errors: bool = False,
    ) -> Any:
        response = self._client.request(method, path, json=body)
        if response.status_code >= 400:
            detail = "" if hide_errors else f": {response.text[:300]}"
            raise LLMError(f"{method} {path} -> HTTP {response.status_code}{detail}")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def info(self) -> dict:
        return self._request("GET", "/api/info")

    def ensure_credential(self, integration_id: str, key: str) -> str | None:
        existing = self._request("GET", "/api/credential", hide_errors=True) or {}
        stale_id = None
        for item in existing.get("data") or []:
            if item.get("integrationID") != integration_id or not item.get("active"):
                continue
            value = item.get("value") or {}
            if value.get("type") == "key" and value.get("key") == key:
                return item.get("id")
            stale_id = item.get("id")
            break
        label = "default" if not stale_id else f"rotated-{int(time.time())}"
        created = (
            self._request(
                "POST",
                "/api/credential",
                body={
                    "integrationID": integration_id,
                    "label": label,
                    "value": {"type": "key", "key": key},
                },
                hide_errors=True,
            )
            or {}
        )
        credential_id = (created.get("data") or {}).get("id")
        if credential_id:
            self._request("POST", f"/api/credential/{credential_id}/activate", hide_errors=True)
            if stale_id and stale_id != credential_id:
                try:
                    self._request("DELETE", f"/api/credential/{stale_id}", hide_errors=True)
                except LLMError:
                    pass
        return credential_id

    def create_session(
        self,
        directory: Path | str,
        *,
        agent: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        created = self._request(
            "POST", "/api/session", body={"location": {"directory": str(directory)}}
        )
        session_id = (created or {}).get("data", {}).get("id")
        if not session_id:
            raise LLMError("session create returned no id")
        if agent:
            self._request("POST", f"/api/session/{session_id}/agent", body={"agent": agent})
        if provider and model:
            self._request(
                "POST",
                f"/api/session/{session_id}/model",
                body={"model": {"providerID": provider, "id": model}},
            )
        return session_id

    def prompt(self, session_id: str, text: str) -> None:
        self._request("POST", f"/api/session/{session_id}/prompt", body={"text": text})

    def wait(self, session_id: str) -> None:
        self._request("POST", f"/api/experimental/session/{session_id}/wait", body={})

    def messages(self, session_id: str) -> list[dict]:
        payload = self._request("GET", f"/api/session/{session_id}/message") or {}
        return payload.get("data") or []

    def session_info(self, session_id: str) -> dict:
        payload = self._request("GET", f"/api/session/{session_id}") or {}
        return payload.get("data") or {}

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", f"/api/session/{session_id}")


class ServerManager:
    """Owns one opencode serve child process for the app runtime."""

    def __init__(self) -> None:
        self.client: OpenCodeClient | None = None
        self.proc: subprocess.Popen | None = None
        self.password: str | None = None
        self._password_event = threading.Event()

    @property
    def base_url(self) -> str:
        return f"http://{config.OPENCODE_HOST}:{config.OPENCODE_PORT}"

    def start(self) -> None:
        prepare_runtime_home()
        log_file = open(config.RUNTIME_LOG, "a", buffering=1)  # noqa: SIM115
        env = {**os.environ, "HOME": str(config.RUNTIME_HOME)}
        self.proc = subprocess.Popen(
            [
                config.OPENCODE_BIN,
                "serve",
                "--hostname",
                config.OPENCODE_HOST,
                "--port",
                str(config.OPENCODE_PORT),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(config.RUNTIME_WORKSPACE),
            env=env,
        )

        def read_output() -> None:
            assert self.proc and self.proc.stdout
            for line in self.proc.stdout:
                clean = _SECRET_RE.sub("<redacted-secret>", line)
                if "server password" in clean:
                    match = _PASSWORD_RE.search(line)
                    if match:
                        self.password = match.group(1)
                        self._password_event.set()
                    clean = "server password <redacted>\n"
                log_file.write(clean)

        threading.Thread(target=read_output, daemon=True).start()
        if not self._password_event.wait(timeout=30):
            raise LLMError(f"opencode server did not announce a password; see {config.RUNTIME_LOG}")
        deadline = time.time() + 30
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                probe = httpx.get(
                    self.base_url + "/api/info",
                    auth=("opencode", self.password or ""),
                    timeout=2,
                )
                if probe.status_code == 200:
                    self.client = OpenCodeClient(self.base_url, self.password or "")
                    return
            except httpx.HTTPError as exc:
                last_error = exc
            time.sleep(0.4)
        raise LLMError(f"opencode server never became ready ({last_error})")

    def ensure_credential(self) -> None:
        if self.client is None:
            raise LLMError("server not started")
        key = _resolve_provider_key()
        if not key:
            raise LLMError(
                "No OpenCode Go key. Set OPENCODE_GO_KEY, or OPENCODE_GO_KEY_FILE "
                "pointing at the OpenCode auth.json."
            )
        self.client.ensure_credential(config.PROVIDER_ID, key)

    def stop(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    # -- convenience -------------------------------------------------------
    def create_session(self, *, agent: str | None = None, model: bool = True) -> str:
        if self.client is None:
            raise LLMError("server not started")
        workspace = (
            config.RUNTIME_WORKSPACE
            if tenancy.user_id() == tenancy.LEGACY_ID
            else config.RUNTIME_DIR / "workspaces" / tenancy.user_id()
        )
        workspace.mkdir(parents=True, exist_ok=True)
        return self.client.create_session(
            workspace,
            agent=agent,
            provider=config.PROVIDER_ID if model else None,
            model=config.MODEL_ID if model else None,
        )

    def run_turn(self, session_id: str, text: str) -> dict:
        """Prompt, wait, then return the new normalized messages and usage delta."""
        if self.client is None:
            raise LLMError("server not started")
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
        raw_messages.reverse()  # the API returns newest-first
        normalized = normalize_messages(raw_messages)
        for message in normalized:
            if message["role"] == "user" and message["text"].startswith("[ACCOUNT_CONTEXT]"):
                message["text"] = message["text"].split("[/ACCOUNT_CONTEXT]\n[USER_MESSAGE]\n", 1)[
                    -1
                ]
        assistant = [message for message in normalized if message["role"] == "assistant"]
        if not assistant:
            raise LLMError("the model returned no assistant reply")
        if assistant[-1].get("error"):
            error = assistant[-1]["error"] or {}
            raise LLMError(f"model error: {error.get('type', 'provider_error')}")
        after = self.client.session_info(session_id)
        usage = _usage_delta(before, after)
        return {"messages": normalized, "usage": usage, "session": after}

    def generate(self, prompt: str, *, agent: str, timeout_note: str = "") -> tuple[str, dict]:
        """One-shot generation in a throwaway session; returns (text, usage)."""
        if self.client is None:
            raise LLMError("server not started")
        session_id = self.create_session(agent=agent)
        try:
            self.client.prompt(session_id, prompt)
            self.client.wait(session_id)
            raw_messages = self.client.messages(session_id)
            raw_messages.reverse()  # the API returns newest-first
            normalized = normalize_messages(raw_messages)
            assistant = [m for m in normalized if m["role"] == "assistant"]
            info = self.client.session_info(session_id)
            usage = _usage_delta({}, info)
            if not assistant:
                raise LLMError(
                    "the model returned no assistant message"
                    + (timeout_note and f" ({timeout_note})")
                )
            if assistant[-1].get("error"):
                error = assistant[-1]["error"] or {}
                raise LLMError(f"model error: {error.get('type', 'provider_error')}")
            return assistant[-1]["text"].strip(), usage
        finally:
            try:
                self.client.delete_session(session_id)
            except LLMError:
                pass


def _resolve_provider_key() -> str:
    """Provider key from env, or from a local OpenCode auth store (dev only)."""
    if config.OPENCODE_GO_KEY:
        return config.OPENCODE_GO_KEY
    if not config.OPENCODE_GO_KEY_FILE:
        return ""
    path = Path(config.OPENCODE_GO_KEY_FILE).expanduser()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return ""
    entry = data.get(config.PROVIDER_ID) or {}
    key = entry.get("key") if isinstance(entry, dict) else None
    return key if isinstance(key, str) else ""


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

    before_tokens = (
        tokens(before)
        if before
        else {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "cache_write": 0,
            "cost_usd": 0.0,
        }
    )
    after_tokens = tokens(after)
    return {
        key: after_tokens[key] - before_tokens[key]
        for key in ("input_tokens", "output_tokens", "cache_read", "cache_write", "cost_usd")
    }
