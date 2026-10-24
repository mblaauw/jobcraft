"""Validated deterministic blend configuration; never part of paid cache keys."""

import math
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Weights:
    rule: float = 0.35
    fit: float = 0.40
    llm: float = 0.25

    def __post_init__(self) -> None:
        values = self.rule, self.fit, self.llm
        if any(not math.isfinite(value) or value < 0 for value in values) or sum(values) <= 0:
            raise ValueError(
                "Scoring weights must be finite, nonnegative and have a positive total"
            )


def read_weights() -> Weights:
    """MATCH_WEIGHT_RULE/FIT/LLM override defaults, with missing parts renormalized."""
    defaults = Weights()
    return Weights(
        **{
            key: float(os.environ.get(f"MATCH_WEIGHT_{key.upper()}", str(getattr(defaults, key))))
            for key in ("rule", "fit", "llm")
        }
    )
