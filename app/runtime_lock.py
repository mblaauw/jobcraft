"""Single-process ownership of a workspace, held before any startup mutation."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_data_dir(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / ".jobcraft.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Jobcraft is already running for this data directory; use one uvicorn worker."
            ) from None
        try:
            yield
        finally:
            # Keep the inode: unlinking would allow a second process to lock a new file.
            fcntl.flock(lock, fcntl.LOCK_UN)
