#!/usr/bin/env python
"""Synthetic timing harness for vote/page hot paths. Never touches real data."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(tempfile.mkdtemp(prefix="jobcraft-bench-"))
os.environ["APP_DATA_DIR"] = str(ROOT / "data")
os.environ["APP_RUNTIME_DIR"] = str(ROOT / "runtime")
os.environ["APP_SEED_DIR"] = str(ROOT / "seed")
os.environ["APP_SKIP_LLM"] = "1"
os.environ["RUNWARE_API_KEY"] = ""
os.environ["OPENCODE_GO_KEY"] = ""

from app import feedback, profile, ranking, store  # noqa: E402


def timed(label, fn):
    start = time.perf_counter()
    result = fn()
    print(f"{label}: {(time.perf_counter() - start) * 1000:.0f} ms")
    return result


def main() -> int:
    store.init_db()
    profile.write_preferences(
        {
            "home": {"label": "Amsterdam"},
            "hard_filters": {"hours_min": 24, "hours_max": 32},
        }
    )
    description = " ".join(f"woord{i}" for i in range(800))
    for i in range(2000):
        store.upsert_job(
            {
                "source_id": "bench",
                "url": f"https://example.test/bench/{i}",
                "title": f"Benchmark vacature {i}",
                "company": f"Bedrijf {i % 50}",
                "location": ["Amsterdam", "Utrecht", "Groningen", ""][i % 4],
                "hours": "24 uur",
                "description": description,
                "posted_at": "2026-10-01T10:00:00",
                "content_hash": f"bench-{i}",
            }
        )
    timed("feedback.rebuild", feedback.rebuild)
    timed("rerank_all", ranking.rerank_all)
    digest = ranking.matching.current_digest()
    timed("queue_depth", lambda: ranking.queue_depth(digest))
    timed("rerank_one", lambda: ranking.rerank_one(1))
    print(f"temporary data: {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
