"""The profile's history, with what each change actually changed."""

from __future__ import annotations

import difflib
from pathlib import Path

from . import config, profile, store

MAX_LINES = 30


def _read(row) -> str | None:
    path = Path(row["path"]).resolve()
    if not path.is_relative_to(config.user_path(profile.HISTORY_DIR).resolve()) or not path.exists():
        return None
    return path.read_text()


def history(limit: int = 15) -> list[dict]:
    """Newest first. Each version is the text before a change; the change made the next newer text."""
    rows = store.list_profile_versions(limit)
    newer = profile.read_profile()
    found = []
    for row in rows:
        before = _read(row)
        lines: list[tuple[str, str]] = []
        if before is not None:
            for line in difflib.unified_diff(before.splitlines(), newer.splitlines(), lineterm="", n=0):
                if line.startswith(("+++", "---", "@@")) or not line[1:].strip():
                    continue
                lines.append(("add" if line[0] == "+" else "del", line[1:]))
            newer = before
        found.append({
            "id": row["id"],
            "created_at": row["created_at"],
            "note": row["note"] or "",
            "added": sum(kind == "add" for kind, _ in lines),
            "removed": sum(kind == "del" for kind, _ in lines),
            "lines": lines[:MAX_LINES],
            "more": max(0, len(lines) - MAX_LINES),
        })
    return found
