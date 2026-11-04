"""One decision per vacancy: what happens next, and why.

Jev (System One) answers typed questions with a certainty and the matcher (the LLM)
judges in prose. This is the piece in between: the rules that say which of the two a
vacancy needs, in one place, so the gate can be tested, counted and measured. Pure:
callers pass what they already know, nothing is read from the store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from ..contracts import JobRow

Outcome = Literal["excluded", "verdict", "wait", "act", "escalate"]
OUTCOMES: tuple[Outcome, ...] = ("excluded", "verdict", "wait", "act", "escalate")

# What a page or a round summary calls each outcome.
LABELS = {
    "excluded": "niet van toepassing",
    "verdict": "uitgebreid beoordeeld",
    "wait": "nog geen snelle beoordeling",
    "act": "snelle beoordeling volstaat",
    "escalate": "uitgebreide beoordeling nodig",
}

HANDLED_STATUSES = ("ignored", "applied", "interview", "offer", "rejected")


@dataclass(frozen=True)
class Thresholds:
    """Read at call time under the names deployments already use."""

    min_rank: float = 35.0  # MATCH_MIN_RANK: below this a verdict is never worth buying
    confidence: float = 0.75  # SYSTEMONE_CONFIDENCE: from here Jev's fit stands on its own
    sample: int = 2  # MATCH_SAMPLE: sure fits judged per round to keep the calibration honest


def read_thresholds() -> Thresholds:
    defaults = Thresholds()

    def number(name: str, fallback: float) -> float:
        try:
            return float(os.environ.get(name, fallback))
        except ValueError:
            return fallback

    return Thresholds(
        min_rank=number("MATCH_MIN_RANK", defaults.min_rank),
        confidence=number("SYSTEMONE_CONFIDENCE", defaults.confidence),
        sample=max(0, int(number("MATCH_SAMPLE", defaults.sample))),
    )


@dataclass(frozen=True)
class Route:
    outcome: Outcome
    code: str  # which rule decided; stable for tests, counts and the sample
    reason: str  # the same, in the person's words


def _value(job, key: str):
    try:
        return job[key]
    except (KeyError, IndexError):
        return None


def certain(signals: dict | None, thresholds: Thresholds | None = None) -> bool:
    """Jev gave a fit and is sure enough about it to stand on its own."""
    if not signals or signals.get("fit") is None:
        return False
    limit = (thresholds or read_thresholds()).confidence
    try:
        return float(signals.get("confidence") or 0.0) >= limit
    except (TypeError, ValueError):
        return False


def route(
    job: JobRow,
    signals: dict | None,
    *,
    fresh: bool,
    rank: float | None,
    too_far: bool = False,
    wanted: bool = False,
    quick: bool = True,
    thresholds: Thresholds | None = None,
) -> Route:
    """First rule that applies wins.

    `fresh`: the stored verdict belongs to the current profile and prompt.
    `rank`: the deterministic rank; None skips the floor (the caller normally has one).
    `wanted`: the person asked for a deeper assessment of this vacancy.
    `quick`: a quick assessment can still come (Jev is configured); without one, a
    vacancy that has none goes to the matcher as before, instead of waiting forever.
    """
    limits = thresholds or read_thresholds()
    if _value(job, "vote") or _value(job, "status") in HANDLED_STATUSES:
        return Route("excluded", "handled", "al door jou beoordeeld of afgehandeld")
    if too_far:
        return Route("excluded", "too_far", "te ver weg")
    if fresh:
        return Route("verdict", "fresh", "uitgebreide beoordeling is actueel")
    if wanted:
        return Route("escalate", "wanted", "je vroeg om een diepere beoordeling")
    if not signals or signals.get("fit") is None:
        if quick:
            return Route("wait", "no_signals", "nog geen snelle beoordeling")
        if rank is not None and rank < limits.min_rank:
            return Route("act", "low_rank", "te laag gerangschikt voor een uitgebreide beoordeling")
        return Route("escalate", "no_quick", "geen snelle beoordeling mogelijk")
    if rank is not None and rank < limits.min_rank:
        return Route("act", "low_rank", "te laag gerangschikt voor een uitgebreide beoordeling")
    if certain(signals, limits):
        return Route("act", "sure", "de snelle beoordeling is zeker genoeg")
    return Route("escalate", "unsure", "de snelle beoordeling twijfelt")
