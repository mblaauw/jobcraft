"""What happened to each vacancy, per account, in order: an append-only history.

Until now only the latest state was kept (one status, one judgement, one verdict), so
nothing could say how a vacancy got there or how often a route was right. Votes already
have their own history in ``feedback``. Values are short codes and numbers, never text
the person wrote.

The history that is already on disk is carried over: the date each application status
was first reached, and each quick judgement with its reason.
"""

from __future__ import annotations

import json
import sqlite3


def _at(day: str) -> str | None:
    """A stored ``YYYY-MM-DD`` as the start of that day, in the format of every other row."""
    return f"{day}T00:00:00+00:00" if isinstance(day, str) and len(day) == 10 else None


def _backfill(con: sqlite3.Connection) -> None:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_meta'").fetchone():
        return  # nothing was stored per account yet
    known = {row[0] for row in con.execute("SELECT id FROM jobs")}
    rows = []
    for owner, key, value in con.execute(
        "SELECT owner, key, value FROM user_meta WHERE key LIKE 'application:%' OR key LIKE 'judgement:%'"
    ):
        try:
            job_id, data = int(key.split(":", 1)[1]), json.loads(value)
        except (ValueError, TypeError):
            continue
        if job_id not in known or not isinstance(data, dict):
            continue
        if key.startswith("application:"):
            since = data.get("since") if isinstance(data.get("since"), dict) else {}
            for status, day in sorted(since.items(), key=lambda item: str(item[1])):
                if (at := _at(day)) is not None:
                    rows.append((owner, job_id, "status", str(status), at))
        else:
            if data.get("answer") and data.get("at"):
                rows.append((owner, job_id, "judgement", str(data["answer"]), str(data["at"])))
            if data.get("reason") and data.get("reason_at"):
                rows.append((owner, job_id, "reason", str(data["reason"]), str(data["reason_at"])))
    rows.sort(key=lambda row: row[4])
    con.executemany(
        "INSERT INTO job_events(owner, job_id, kind, value, at, detail) VALUES (?,?,?,?,?,'{\"backfill\":true}')",
        rows,
    )


def up(con: sqlite3.Connection) -> None:
    # Caller owns BEGIN/COMMIT; no executescript (which can implicitly commit).
    existed = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_events'").fetchone()
    con.execute(
        "CREATE TABLE IF NOT EXISTS job_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "owner TEXT NOT NULL, "
        "job_id INTEGER NOT NULL REFERENCES jobs(id), "
        "kind TEXT NOT NULL, "
        "value TEXT NOT NULL, "
        "previous TEXT, "
        "at TEXT NOT NULL, "
        "detail TEXT)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS job_events_kind ON job_events(owner, kind, job_id, id)")
    con.execute("CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id)")
    if not existed:  # a replayed migration must not carry the history over twice
        _backfill(con)
