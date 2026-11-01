"""Facts about a vacancy, asked once and shared by all accounts.

These hold answers about public vacancy text only, so they live in the shared meta table.
"""

from __future__ import annotations

import json

from .connection import connect

_PREFIX = "job_facts:"


def load_job_facts() -> dict[int, dict]:
    """All stored facts by job id; one read per matching round."""
    with connect() as con:
        rows = con.execute(
            "SELECT key, value FROM meta WHERE key LIKE ?", (_PREFIX + "%",)
        ).fetchall()
    facts: dict[int, dict] = {}
    for row in rows:
        try:
            facts[int(row["key"][len(_PREFIX) :])] = json.loads(row["value"])
        except ValueError:
            continue
    return facts


def save_job_facts(job_id: int, key: str, answers: dict) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO meta(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"{_PREFIX}{job_id}", json.dumps({"key": key, "answers": answers}, ensure_ascii=False)),
        )


def delete_job_facts(job_ids: list[int]) -> None:
    with connect() as con:
        con.executemany("DELETE FROM meta WHERE key=?", [(f"{_PREFIX}{i}",) for i in job_ids])
