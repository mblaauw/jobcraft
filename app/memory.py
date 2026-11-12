"""Keep the worker inside its container's memory limit.

A pass over the whole catalogue holds every vacancy text at once (some 400 MB at 20,000
vacancies). Two of those side by side in different worker threads, next to the embedding
child, filled the 5 GB limit on the VPS. And glibc keeps what Python frees for reuse
instead of returning it, while the container limit counts it all the same: an idle worker
kept 3.5 GB. So passes take turns, and memory goes back to the system after each task.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

log = logging.getLogger("jobcraft.memory")

# Reentrant: a re-rank runs the routed pass inside itself, and the match round calls both.
_CATALOGUE = threading.RLock()
_thread = threading.local()
_task_threads = False  # set once this process runs task threads (APP_ROLE worker or all)

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


_trimming = threading.Lock()
# Polled every few seconds by every open tab, or served from disk: nothing to give back.
_QUIET = ("/static/", "/healthz", "/api/werk")


def _release_unless_running() -> None:
    if _trimming.acquire(blocking=False):  # one trim gives back everything; a second adds nothing
        try:
            release()
        finally:
            _trimming.release()


async def give_back_after_request(request, call_next):
    """The web process the same as the worker: a page over the whole catalogue (the list, the
    dashboard, a vote's re-blend) frees what it built, but glibc keeps it per thread. Requests
    land on dozens of threads, so a web process grew to 640 MB of its 768 MB in a day and was
    killed. The trim runs beside the response, so nobody waits for it."""
    response = await call_next(request)
    if not request.url.path.startswith(_QUIET):
        asyncio.get_running_loop().run_in_executor(None, _release_unless_running)
    return response


def mark_task_thread() -> None:
    """Called by each task thread. Where task threads run, only they take turns, so a request
    handled in the same process (APP_ROLE=all) never waits behind a worker's pass; in a web
    process without them, requests take turns among themselves."""
    global _task_threads
    _task_threads = True
    _thread.task = True


@contextmanager
def catalogue() -> Iterator[None]:
    """One whole-catalogue pass at a time; what it freed goes back before the next one starts,
    which may run in another thread whose allocator cannot reuse this thread's free memory."""
    if _task_threads and not getattr(_thread, "task", False):
        try:
            yield
        finally:
            release()
        return
    with _CATALOGUE:
        try:
            yield
        finally:
            release()
