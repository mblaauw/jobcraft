"""Feedback-free paid verdict digest primitives."""

from __future__ import annotations

import hashlib
import json

from .. import profile
from ..contracts import JobRow

PROFILE_PROMPT_LIMIT = 4000
VERDICT_PROMPT_VERSION = "verdict-2026-10-24-public-preferences-v2"


STABLE_KEY = "verdict_key"
STABLE_VERSION = "prompt-inputs-1"


def current_digest(
    preferences: dict | None = None,
    profile_excerpt: str | None = None,
    *,
    prompt_version: str = VERDICT_PROMPT_VERSION,
) -> str:
    prefs = preferences if preferences is not None else profile.read_preferences()
    excerpt = public_profile_digest(profile_excerpt)
    digest = hashlib.sha256(f"{profile_hash(prefs)}:{excerpt}:{prompt_version}".encode()).hexdigest()[:20]
    _adopt(prefs, excerpt, prompt_version, digest)
    return digest


def profile_hash(preferences: dict) -> str:
    """What a verdict prompt reads from the preferences, and nothing else.

    Not the travel budgets, private notes or the evidence behind a like: moving the reach
    slider or editing a note used to make every paid verdict of the account stale at once.
    Where someone lives stays in: the prompt states the travel time from there."""
    home = preferences.get("home") or {}
    stable = {
        "public": profile.public_preferences(preferences),
        "home": {key: home.get(key) for key in ("label", "lat", "lon", "bike_kind")},
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _profile_hash_until_2026_11(preferences: dict) -> str:
    """The key verdicts were stored under before; kept only to recognise them once."""
    stable = {
        key: value for key, value in preferences.items() if key not in ("updated_at", "wishes")
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def _adopt(preferences: dict, excerpt: str, prompt_version: str, digest: str) -> None:
    """Once per account: verdicts that are current under the old key get the new one, so the
    change of key itself buys no verdict again."""
    from .. import store

    if store.meta_get(STABLE_KEY) == STABLE_VERSION:
        return
    before = hashlib.sha256(
        f"{_profile_hash_until_2026_11(preferences)}:{excerpt}:{prompt_version}".encode()
    ).hexdigest()[:20]
    store.restamp_verdicts(lambda job: job_match_digest(before, job), lambda job: job_match_digest(digest, job))
    store.meta_set(STABLE_KEY, STABLE_VERSION)


def public_profile_digest(excerpt: str | None = None) -> str:
    text = profile.read_public_excerpt(PROFILE_PROMPT_LIMIT * 2) if excerpt is None else excerpt
    return hashlib.sha256(text[:PROFILE_PROMPT_LIMIT].encode()).hexdigest()[:16]


def job_match_digest(profile_digest: str, job: JobRow) -> str:
    """Fingerprint of everything that feeds one LLM verdict (no feedback)."""
    raw = f"{profile_digest}:{job['content_hash'] or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
