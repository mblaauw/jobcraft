"""The daily AI limit per account, shared by everything that makes paid calls."""

from __future__ import annotations

import os

from . import store


def budget_reached() -> bool:
    budget = float(os.environ.get("MATCH_DAILY_BUDGET_USD", "0.50") or 0)
    if budget > 0 and store.daily_usage_cost() >= budget:
        return True
    # Many gateways report no price, so spend in dollars can stay 0 forever.
    tokens = int(os.environ.get("AI_DAILY_TOKEN_LIMIT", "500000") or 0)
    return tokens > 0 and store.daily_usage_tokens() >= tokens
