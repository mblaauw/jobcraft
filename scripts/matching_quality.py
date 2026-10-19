#!/usr/bin/env python
"""Measure how well the cheap rankers agree with the LLM verdicts.

Usage:
    uv run python scripts/matching_quality.py                 # the legacy account
    uv run python scripts/matching_quality.py --user test-user

Reports, for one account's jobs that have an LLM verdict:
- top-N overlap between the deterministic rank and the LLM score
- Spearman-style rank correlation
- paired Jev fit vs LLM score (only jobs with both)

Use this before changing the rank weights or the Jev question set: the LLM
verdicts are the (expensive) reference signal we are trying to approach cheaply.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import store, tenancy  # noqa: E402  (after the path fix)


def spearman(rows, left: str, right: str) -> float:
    ordered_left = sorted(rows, key=lambda row: row[left] or 0, reverse=True)
    ordered_right = sorted(rows, key=lambda row: row[right] or 0, reverse=True)
    pos_left = {row["id"]: index for index, row in enumerate(ordered_left)}
    pos_right = {row["id"]: index for index, row in enumerate(ordered_right)}
    n = len(rows)
    if n < 3:
        return 0.0
    d2 = sum((pos_left[row["id"]] - pos_right[row["id"]]) ** 2 for row in rows)
    return 1 - (6 * d2) / (n * (n * n - 1))


def resolve_owner(username: str | None) -> tuple[str, str]:
    """Map a username to its opaque owner id; no username means the legacy account."""
    if not username:
        return tenancy.LEGACY_ID, "Legacy account"
    try:
        with store.connect() as con:
            row = con.execute(
                "SELECT id, username, display_name FROM users WHERE username=? COLLATE NOCASE",
                (username,),
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        raise SystemExit(f"Onbekende gebruiker: {username}")
    return row["id"], row["display_name"] or row["username"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user",
        help="gebruikersnaam van het account (standaard: bestaande legacy-werkruimte)",
    )
    args = parser.parse_args(argv)
    owner, label = resolve_owner(args.user)
    rows = store.list_scored_jobs(owner)
    print(f"account: {label} ({owner})")
    print(f"jobs with an LLM verdict and a rank: {len(rows)}")
    if not rows:
        return 0
    for top_n in (20, 50, 100):
        by_rank = {
            r["id"] for r in sorted(rows, key=lambda r: r["rank_score"], reverse=True)[:top_n]
        }
        by_llm = {
            r["id"] for r in sorted(rows, key=lambda r: r["match_score"], reverse=True)[:top_n]
        }
        overlap = len(by_rank & by_llm)
        print(f"  top-{top_n:<3} overlap: {overlap:3d}/{top_n} ({overlap / top_n * 100:.0f}%)")
    print(f"  rank correlation (rank vs LLM): {spearman(rows, 'rank_score', 'match_score'):.3f}")

    paired = []
    for row in rows:
        raw = row["typed_signals"]
        if not raw:
            continue
        try:
            fit = json.loads(raw).get("fit")
        except ValueError:
            fit = None
        if fit is not None:
            paired.append({"id": row["id"], "fit": fit, "llm": row["match_score"]})
    if paired:
        print(f"\njobs with both Jev fit and LLM verdict: {len(paired)}")
        print(f"  fit correlation (fit vs LLM): {spearman(paired, 'fit', 'llm'):.3f}")
        print(
            f"  mean fit {sum(p['fit'] for p in paired) / len(paired):.1f} | "
            f"mean LLM {sum(p['llm'] for p in paired) / len(paired):.1f}"
        )
    else:
        print(
            "\nNo paired Jev fit / LLM verdicts yet — run a match round that covers LLM-verified jobs."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
