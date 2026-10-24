"""Feedback-free paid verdict digest primitives."""

from __future__ import annotations

import hashlib
import json

from .. import profile
from ..contracts import JobRow

PROFILE_PROMPT_LIMIT = 4000
VERDICT_PROMPT_VERSION = "verdict-2026-10-24-public-preferences-v2"


def current_digest(
    preferences: dict | None = None,
    profile_excerpt: str | None = None,
    *,
    prompt_version: str = VERDICT_PROMPT_VERSION,
) -> str:
    prefs = preferences if preferences is not None else profile.read_preferences()
    raw = f"{profile_hash(prefs)}:{public_profile_digest(profile_excerpt)}:{prompt_version}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def profile_hash(preferences: dict) -> str:
    # updated_at is volatile bookkeeping and must not invalidate cached verdicts.
    stable = {key: value for key, value in preferences.items() if key != "updated_at"}
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def public_profile_digest(excerpt: str | None = None) -> str:
    text = profile.read_public_excerpt(PROFILE_PROMPT_LIMIT * 2) if excerpt is None else excerpt
    return hashlib.sha256(text[:PROFILE_PROMPT_LIMIT].encode()).hexdigest()[:16]


def job_match_digest(profile_digest: str, job: JobRow) -> str:
    """Fingerprint of everything that feeds one LLM verdict (no feedback)."""
    raw = f"{profile_digest}:{job['content_hash'] or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
