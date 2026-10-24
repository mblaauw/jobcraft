"""Cookie-free public GETs: robots, host pacing, bounded retries and validators."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import tempfile
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

from . import config

USER_AGENT = "Jobcraft/1.0 (local-first vacancy research)"
_guard = threading.Lock()
_host_locks: dict[tuple, threading.Lock] = {}
_last_request: dict[tuple, float] = {}


class PoliteClient:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache_dir: Path | None = None,
        delay: float = 0.4,
        retries: int = 2,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        if (
            self.client.auth is not None
            or self.client.cookies
            or any(name.lower() in ("authorization", "cookie") for name in self.client.headers)
        ):
            raise ValueError("Source transport accepts only cookie-free public clients")
        self.owned = client is None
        self.cache_dir = cache_dir if cache_dir is not None else config.RUNTIME_DIR / "http-cache"
        self.delay, self.retries, self.sleep, self.clock = max(0, delay), retries, sleep, clock
        self.robots: dict[str, RobotFileParser] = {}

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        if self.owned:
            self.client.close()

    def _wait(self, origin: str, delay: float) -> None:
        key = self.clock, origin
        with _guard:
            lock = _host_locks.setdefault(key, threading.Lock())
        with lock:
            previous = _last_request.get(key)
            if previous is not None:
                self.sleep(max(0, previous + delay - self.clock()))
            _last_request[key] = self.clock()

    def _request(self, url: str, *, headers=None, delay=None) -> httpx.Response:
        origin = str(httpx.URL(url).copy_with(path="/", query=None, fragment=None)).rstrip("/")
        for attempt in range(self.retries + 1):
            self._wait(origin, self.delay if delay is None else delay)
            self.client.cookies.clear()
            outgoing = httpx.Headers(headers or {})
            outgoing["User-Agent"] = USER_AGENT
            try:
                response = self.client.get(url, headers=outgoing, follow_redirects=False)
            except httpx.RequestError:
                if attempt == self.retries:
                    raise
                self.sleep(min(2**attempt, 8))
                continue
            finally:
                self.client.cookies.clear()
            if (
                response.status_code != 429 and not 500 <= response.status_code <= 599
            ) or attempt == self.retries:
                return response
            retry = response.headers.get("Retry-After", "")
            try:
                pause = float(retry)
            except ValueError:
                try:
                    pause = (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    pause = 2**attempt
            self.sleep(max(0, min(pause if math.isfinite(pause) else 2**attempt, 30)))
        raise RuntimeError("Unreachable retry state")

    def _robots(self, url: str) -> RobotFileParser:
        origin = str(httpx.URL(url).copy_with(path="/", query=None, fragment=None)).rstrip("/")
        if origin not in self.robots:
            target = origin + "/robots.txt"
            response = self._request(target)
            policy = RobotFileParser(target)
            if response.status_code == 404:
                policy.parse([])  # authoritative missing policy, not transient unavailability
            elif response.status_code in (401, 403):
                policy.parse(["User-agent: *", "Disallow: /"])
            elif 200 <= response.status_code < 300:
                policy.parse(response.text.splitlines())
            else:
                raise httpx.HTTPStatusError(
                    "Robots policy unavailable", request=response.request, response=response
                )
            self.robots[origin] = policy
        return self.robots[origin]

    def _load(self, key: str) -> dict | None:
        path = self.cache_dir / (key + ".json")
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                return None
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                return None
            if not isinstance(data.get("headers"), dict) or not isinstance(data.get("body"), str):
                return None
            if not all(
                isinstance(k, str) and isinstance(v, str) for k, v in data["headers"].items()
            ):
                return None
            base64.b64decode(data["body"], validate=True)
            return data
        except (OSError, ValueError, TypeError):
            return None

    def _save(self, key: str, response: httpx.Response) -> None:
        data = {
            "body": base64.b64encode(response.content).decode(),
            "headers": {
                name: response.headers[name]
                for name in ("etag", "last-modified", "content-type")
                if name in response.headers
            },
        }
        encoded = json.dumps(data).encode()
        if len(encoded) > 2 * 1024 * 1024:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.cache_dir, delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(encoded)
                output.close()
                os.replace(temporary, self.cache_dir / (key + ".json"))
            finally:
                temporary.unlink(missing_ok=True)
        for path in sorted(
            self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )[64:]:
            path.unlink(missing_ok=True)

    def get(self, url: str, *, params=None, headers=None) -> httpx.Response:
        target = str(httpx.URL(url, params=params)) if params is not None else str(httpx.URL(url))
        outgoing = httpx.Headers(self.client.headers)
        outgoing.update(headers or {})
        outgoing["User-Agent"] = USER_AGENT
        if self.client.auth is not None or any(
            name in outgoing for name in ("authorization", "cookie")
        ):
            raise ValueError("Source transport supports public requests only")
        caller_condition = any(name in outgoing for name in ("if-none-match", "if-modified-since"))
        for _ in range(6):
            parsed = httpx.URL(target)
            if parsed.scheme not in ("http", "https") or parsed.userinfo:
                raise ValueError("Only public HTTP(S) source URLs are supported")
            policy = self._robots(target)
            if not policy.can_fetch(USER_AGENT, target):
                raise httpx.RequestError(
                    "Robots policy disallows this source URL", request=httpx.Request("GET", target)
                )
            key = hashlib.sha256(
                json.dumps([target, sorted(outgoing.items())]).encode()
            ).hexdigest()
            cached = None if caller_condition else self._load(key)
            conditional = dict(outgoing)
            if cached:
                for source, destination in [
                    ("etag", "If-None-Match"),
                    ("last-modified", "If-Modified-Since"),
                ]:
                    if source in cached["headers"]:
                        conditional[destination] = cached["headers"][source]
            response = self._request(
                target,
                headers=conditional,
                delay=max(self.delay, policy.crawl_delay(USER_AGENT) or 0),
            )
            if response.status_code in (301, 302, 303, 307, 308):
                if "location" not in response.headers:
                    raise httpx.RequestError(
                        "Source redirect lacks a destination", request=response.request
                    )
                target = urljoin(target, response.headers.get("location", ""))
                continue
            if response.status_code == 304 and cached:
                merged = {**cached["headers"], **dict(response.headers)}
                for name in (
                    "content-length",
                    "transfer-encoding",
                    "content-encoding",
                    "set-cookie",
                ):
                    merged.pop(name, None)
                return httpx.Response(
                    200,
                    headers=merged,
                    content=base64.b64decode(cached["body"]),
                    request=httpx.Request("GET", target),
                )
            control = response.headers.get("cache-control", "").lower()
            if (
                response.status_code == 200
                and not caller_condition
                and "set-cookie" not in response.headers
                and "no-store" not in control
                and "private" not in control
                and response.headers.get("vary") != "*"
            ):
                with suppress(OSError):
                    self._save(key, response)
            return response
        raise httpx.TooManyRedirects(
            "Source redirect limit reached", request=httpx.Request("GET", target)
        )
