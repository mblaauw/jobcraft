"""Ownership of a workspace per role, held before any startup mutation.

The web app and the worker may share a data directory, but each role runs once.
The combined role takes both locks, so it can never overlap either of them.
"""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_FILES = {"web": ".jobcraft.lock", "worker": ".jobcraft-worker.lock"}
ROLES = {"all": ("web", "worker"), "web": ("web",), "worker": ("worker",)}


def _open(directory: Path, name: str):
    directory.mkdir(parents=True, exist_ok=True)
    return os.fdopen(os.open(directory / name, os.O_CREAT | os.O_RDWR, 0o600), "a+b")


@contextmanager
def exclusive_data_dir(directory: Path, role: str = "all") -> Iterator[None]:
    locks = []
    try:
        for part in ROLES[role]:
            lock = _open(directory, LOCK_FILES[part])
            locks.append(lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(
                    f"Jobcraft is already running a {part} process for this data directory; "
                    "use one uvicorn worker."
                ) from None
        yield
    finally:
        # Keep the inodes: unlinking would allow a second process to lock a new file.
        for lock in locks:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


@contextmanager
def initialization(directory: Path) -> Iterator[None]:
    """Roles starting together take turns: only one may migrate or seed at a time."""
    with _open(directory, ".jobcraft-init.lock") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
