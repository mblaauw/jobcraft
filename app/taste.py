"""Per account, from the shared vacancy index: how much a vacancy looks like what this person wants.

Two parts, both free and instant:
- the wish: closeness to the functions this person is looking for;
- the neighbours: a vacancy scores like the vacancies it resembles most that this person
  already judged (votes, saved, ignored) or that the AI judged for them.

A vote is the strongest label, so one thumbs down pulls similar vacancies down at once.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np

from . import job_index, store

NEIGHBOURS = 7
MIN_LABELS = 3
WISH_KEY = "taste_wish"
POSITIVE_STATUSES = ("saved", "applied", "interview", "offer")


def wish_text(preferences: dict) -> str:
    tracks = preferences.get("tracks") or []
    names = [str(track.get("label") or track.get("id") or "") for track in tracks]
    words = [str(word) for track in tracks for word in (track.get("keywords") or [])[:8]]
    likes = [str(item.get("tag") or "") for item in preferences.get("likes") or []]
    return ". ".join(part for part in (", ".join(names), ", ".join(words), ", ".join(likes)) if part.strip(", "))


def wish_vector(preferences: dict) -> np.ndarray | None:
    """Stored per account; only the worker (which has the model) can make a new one."""
    text = wish_text(preferences)
    if not text:
        return None
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    try:
        stored = json.loads(store.meta_get(WISH_KEY) or "{}")
    except ValueError:
        stored = {}
    if stored.get("digest") == digest and stored.get("vector"):
        return np.asarray(stored["vector"], dtype=np.float32)
    if not job_index.model_available():
        return np.asarray(stored["vector"], dtype=np.float32) if stored.get("vector") else None
    vector = job_index.embed([text])[0]
    store.meta_set(WISH_KEY, json.dumps({"digest": digest, "vector": [round(float(x), 5) for x in vector]}))
    return vector


def label(job, fresh_verdict: bool) -> tuple[float, float] | None:
    """(score 0-100, weight) for what this account or its AI verdict says about a vacancy."""
    vote = job["vote"] or 0
    if vote > 0:
        return 95.0, 3.0
    if vote < 0:
        return 5.0, 3.0
    if job["status"] in POSITIVE_STATUSES:
        return 85.0, 2.0
    if job["status"] == "ignored":
        return 15.0, 2.0
    if fresh_verdict:
        return float(job["match_score"]), 1.0
    return None


def _percentile(values: np.ndarray) -> np.ndarray:
    if len(values) < 2:
        return np.full(len(values), 50.0)
    order = values.argsort().argsort()
    return order / (len(values) - 1) * 100.0


def scores(jobs, preferences: dict, is_fresh) -> dict[int, float]:
    """Taste per job id, 0-100; empty when there is no index yet."""
    index = job_index.load()
    if index is None:
        return {}
    ids, matrix = index
    row_of = {int(job_id): row for row, job_id in enumerate(ids)}
    present = [job for job in jobs if job["id"] in row_of]
    if not present:
        return {}
    vectors = matrix[[row_of[job["id"]] for job in present]]
    parts: list[tuple[float, np.ndarray]] = []

    wish = wish_vector(preferences)
    if wish is not None and wish.shape[0] == vectors.shape[1]:
        parts.append((0.4, _percentile(vectors @ wish)))

    labelled = [(position, found) for position, job in enumerate(present) if (found := label(job, is_fresh(job)))]
    if len(labelled) >= MIN_LABELS:
        positions = np.asarray([position for position, _ in labelled])
        values = np.asarray([found[0] for _, found in labelled], dtype=np.float32)
        weights = np.asarray([found[1] for _, found in labelled], dtype=np.float32)
        similarity = vectors @ vectors[positions].T
        similarity[positions, np.arange(len(positions))] = -np.inf  # nobody is their own neighbour
        k = min(NEIGHBOURS, len(positions) - 1)
        nearest = np.argpartition(-similarity, k - 1, axis=1)[:, :k]
        closeness = np.clip(np.take_along_axis(similarity, nearest, axis=1), 0.01, None) * weights[nearest]
        parts.append((0.6, (closeness * values[nearest]).sum(axis=1) / closeness.sum(axis=1)))

    if not parts:
        return {}
    total = sum(weight for weight, _ in parts)
    blended = sum(weight * values for weight, values in parts) / total
    return {job["id"]: round(float(value), 1) for job, value in zip(present, blended, strict=True)}
