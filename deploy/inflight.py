#!/usr/bin/env python3
"""Before a restart: name the rounds that are in flight, so nobody deploys over a scan unknowingly.

    python3 deploy/inflight.py --data data

Standard library only, read-only, and never fatal: without a database or with a locked
one it prints nothing and exits 0. A restart interrupts a running scan or match round;
the worker queues such a round again once, but the hours it ran are lost.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

LABELS = {"scan": "ophaalronde", "match": "beoordelingsronde", "document": "document", "consolidate": "profiel"}


def inflight(database: Path) -> list[str]:
    """One line per task that is queued or running, oldest first."""
    if not database.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2) as con:
            rows = con.execute(
                "SELECT kind, status, started_at, created_at FROM tasks "
                "WHERE status IN ('queued','running') ORDER BY id"
            ).fetchall()
    except sqlite3.Error:
        return []
    lines = []
    for kind, status, started_at, created_at in rows:
        label = LABELS.get(kind, kind)
        moment = (started_at or created_at or "")[:16].replace("T", " ")
        lines.append(f"{label} {'loopt sinds' if status == 'running' else 'wacht sinds'} {moment}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data", help="the data folder that holds app.db")
    args = parser.parse_args(argv)
    lines = inflight(Path(args.data) / "app.db")
    if lines:
        print("!! werk in uitvoering; een herstart onderbreekt het (de worker zet een ronde eenmaal terug in de wachtrij):")
        for line in lines:
            print(f"   {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
