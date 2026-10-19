"""Small shared helpers used across the app."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Amsterdam")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def parse_json_object(text: str) -> dict:
    """Extract the first JSON object from a model reply, tolerating fences."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model reply")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model reply is not a JSON object")
    return data
