"""Keep the worker inside its container's memory limit.

A pass over the whole catalogue holds every vacancy text at once (some 400 MB at 20,000
vacancies). Two of those side by side in different worker threads, next to the embedding
child, filled the 5 GB limit on the VPS. And glibc keeps what Python frees for reuse
instead of returning it, while the container limit counts it all the same: an idle worker
kept 3.5 GB. So passes take turns, and memory goes back to the system after each task.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

log = logging.getLogger("jobcraft.memory")

# Reentrant: a re-rank runs the routed pass inside itself, and the match round calls both.
_CATALOGUE = threading.RLock()

try:
    _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
    _libc.malloc_trim  # glibc only; macOS and musl have no such call  # noqa: B018
except (OSError, AttributeError):
    _libc = None


def release() -> None:
    """Hand freed heap memory back to the system; a no-op where libc cannot."""
    if _libc is None:
        return
    try:
        _libc.malloc_trim(0)
    except Exception:  # bookkeeping must never fail the task it follows
        log.debug("malloc_trim failed", exc_info=True)


@contextmanager
def catalogue() -> Iterator[None]:
    """One whole-catalogue pass at a time; what it freed goes back before the next one starts,
    which may run in another thread whose allocator cannot reuse this thread's free memory."""
    with _CATALOGUE:
        try:
            yield
        finally:
            release()
