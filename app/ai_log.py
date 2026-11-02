"""What each AI call received and returned, on disk per account and readable by admins.

One JSON line per call in `<workspace>/ai-log/YYYY-MM-DD.jsonl`. Living in the account's
own folder means an account export and deletion include it without extra code. Files older
than `AI_LOG_DAYS` are removed, so a prompt does not stay around forever.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import config, tenancy

log = logging.getLogger("jobcraft.llm")

MAX_FIELD_CHARS = 60_000
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_OWNER = re.compile(r"[\w-]{1,80}")


def _retention_days() -> int:
    try:
        return max(1, int(os.environ.get("AI_LOG_DAYS", "30")))
    except ValueError:
        return 30


def _directory(owner: str | None = None) -> Path:
    owner = owner or tenancy.user_id()
    if owner == tenancy.LEGACY_ID:
        return config.DATA_DIR / "ai-log"
    return config.DATA_DIR / "users" / owner / "ai-log"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n[... {len(text) - limit} tekens ingekort]"


def _prune(directory: Path, today: datetime) -> None:
    cutoff = (today - timedelta(days=_retention_days())).strftime("%Y-%m-%d")
    for path in directory.glob("*.jsonl"):
        if path.stem < cutoff:
            path.unlink(missing_ok=True)


def record(
    agent: str,
    model: str,
    messages: list[dict],
    output: str = "",
    *,
    usage: dict | None = None,
    duration_ms: int = 0,
    error: str = "",
    ref: str = "",
    limit: int = MAX_FIELD_CHARS,
) -> None:
    """Never let bookkeeping break the call it describes."""
    usage = usage or {}
    log.info(
        "ai call agent=%s model=%s in=%s out=%s ms=%s status=%s",
        agent,
        model,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        duration_ms,
        "error" if error else "ok",
    )
    try:
        now = datetime.now(UTC)
        directory = _directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{now:%Y-%m-%d}.jsonl"
        new_day = not path.exists()
        line = json.dumps(
            {
                "at": now.isoformat(),
                "agent": agent,
                "model": model,
                "ref": ref,
                "input": [
                    {"role": item.get("role"), "content": _clip(str(item.get("content") or ""), limit)}
                    for item in messages
                ],
                "output": _clip(output or "", limit),
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "cost_usd": float(usage.get("cost_usd") or 0.0),
                "duration_ms": duration_ms,
                "error": error[:500],
            },
            ensure_ascii=False,
        )
        # One write on an append-only file: the web and worker processes cannot interleave.
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (line + "\n").encode())
        finally:
            os.close(descriptor)
        if new_day:
            _prune(directory, now)
    except Exception:
        log.exception("could not write the AI log")


def _owners() -> list[str]:
    owners = [tenancy.LEGACY_ID]
    root = config.DATA_DIR / "users"
    if root.is_dir():
        owners += sorted(path.name for path in root.iterdir() if (path / "ai-log").is_dir())
    return owners


def recent(limit: int = 100, *, agent: str = "", owner: str = "", model: str = "") -> list[dict]:
    """Newest first over all accounts, without the bulky prompts."""
    files = [
        (path.stem, name, path)
        for name in ([owner] if owner else _owners())
        for path in _directory(name).glob("*.jsonl")
    ]
    found: list[dict] = []
    for day in sorted({item[0] for item in files}, reverse=True):
        for _day, name, path in (item for item in files if item[0] == day):
            offset = 0
            with path.open("rb") as handle:
                for raw in handle:
                    start, offset = offset, offset + len(raw)
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    if (agent and row.get("agent") != agent) or (model and row.get("model") != model):
                        continue
                    input_chars = sum(len(item.get("content") or "") for item in row.get("input") or [])
                    output_chars = len(row.get("output") or "")
                    row.pop("input", None)
                    row.pop("output", None)
                    found.append(
                        {**row, "owner": name, "day": day, "offset": start,
                         "input_chars": input_chars, "output_chars": output_chars}
                    )
        if len(found) >= limit:
            break
    found.sort(key=lambda row: row.get("at") or "", reverse=True)
    return found[:limit]


def read(owner: str, day: str, offset: int) -> dict | None:
    """One complete call. The path is built only from validated parts."""
    if not _OWNER.fullmatch(owner) or not _DAY.fullmatch(day) or offset < 0:
        return None
    path = _directory(owner) / f"{day}.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            row = json.loads(handle.readline())
    except (OSError, ValueError):
        return None
    return {**row, "owner": owner, "day": day, "offset": offset}
