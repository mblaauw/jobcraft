"""Plain prompt files, cached by modification time."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from . import config


@lru_cache(maxsize=32)
def _read(path: str, mtime: float, size: int) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def load(role: str) -> str:
    path = config.PROMPTS_DIR / f"{role}.md"
    try:
        stat = path.stat()
    except OSError:
        return ""
    return _read(str(path), stat.st_mtime, stat.st_size)
