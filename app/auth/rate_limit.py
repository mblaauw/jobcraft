"""Local authentication: rate limit."""
from __future__ import annotations

import threading
import time

from fastapi import Request

_FAILURE_WINDOW_SECONDS = 900.0



_FAILURE_MAX = 8



_FAILURE_CAP = 2048



_failures: dict[str, list[float]] = {}



_failures_lock = threading.Lock()



def _clock() -> float:
    return time.monotonic()



def _prune_failures(now: float) -> None:
    for key, stamps in list(_failures.items()):
        if not stamps or now - stamps[-1] > _FAILURE_WINDOW_SECONDS:
            _failures.pop(key, None)
    if len(_failures) > _FAILURE_CAP:
        ordered = sorted(_failures.items(), key=lambda item: item[1][-1])
        for key, _ in ordered[: len(_failures) - _FAILURE_CAP]:
            _failures.pop(key, None)



def _failure_key(request: Request, username: str) -> str:
    host = request.client.host if request.client else "?"
    return f"{host}|{username.strip().lower()}"



def _rate_limit_wait(key: str) -> float:
    """Seconds to wait before retrying, or 0. Window-based, so never a permanent lockout."""
    with _failures_lock:
        now = _clock()
        _prune_failures(now)
        stamps = [
            stamp for stamp in _failures.get(key, []) if now - stamp <= _FAILURE_WINDOW_SECONDS
        ]
        if len(stamps) < _FAILURE_MAX:
            return 0.0
        return max(0.0, _FAILURE_WINDOW_SECONDS - (now - min(stamps)))



def _record_failure(key: str) -> None:
    with _failures_lock:
        now = _clock()
        _prune_failures(now)
        _failures.setdefault(key, []).append(now)



def _clear_failures(key: str) -> None:
    with _failures_lock:
        _failures.pop(key, None)

