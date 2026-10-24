"""SQLite job_state operations."""

from __future__ import annotations

from .. import tenancy
from .connection import connect, now
from .jobs import _job_select


def _set_job_state(con, job_id, **fields):
    allowed = {
        "status",
        "vote",
        "match_score",
        "match_reason",
        "profile_hash",
        "rank_score",
        "typed_signals",
        "signals_key",
        "signal_confidence",
        "travel_key",
        "travel_json",
    }
    if not fields or fields.keys() - allowed:
        raise ValueError("Unknown or empty job state column")
    names = list(fields)
    con.execute(
        "INSERT INTO job_states(owner,job_id,"
        + ",".join(names)
        + ") VALUES ("
        + ",".join("?" for _ in range(len(names) + 2))
        + ") ON CONFLICT(owner,job_id) DO UPDATE SET "
        + ",".join(f"{name}=excluded.{name}" for name in names),
        (tenancy.user_id(), job_id, *fields.values()),
    )


def update_job_signals(
    job_id: int,
    rank_score: float,
    signals_json: str,
    signals_key: str,
    confidence: float,
) -> None:
    with connect() as con:
        _set_job_state(
            con,
            job_id,
            rank_score=rank_score,
            typed_signals=signals_json,
            signals_key=signals_key,
            signal_confidence=confidence,
        )


def get_job_travel(job_id: int, key: str) -> str | None:
    with connect() as con:
        row = con.execute(
            "SELECT travel_json FROM job_states WHERE owner=? AND job_id=? AND travel_key=?",
            (tenancy.user_id(), job_id, key),
        ).fetchone()
    return row["travel_json"] if row else None


def set_job_travel(job_id: int, key: str, payload: str) -> None:
    with connect() as con:
        # Transient scoring inputs need no persisted cache or fabricated parent.
        if con.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
            _set_job_state(con, job_id, travel_key=key, travel_json=payload)


def set_rank_scores(scores: dict[int, float]) -> None:
    """Persist deterministic rank scores for many jobs in one transaction."""
    if not scores:
        return
    with connect() as con:
        for job_id, score in scores.items():
            _set_job_state(con, job_id, rank_score=score)


def set_job_vote(job_id: int, vote: int) -> None:
    """Store the account's up/down vote (1, -1 or 0) plus a learning event."""
    with connect() as con:
        _set_job_state(con, job_id, vote=vote)
        con.execute(
            "INSERT INTO feedback(job_id, vote, created_at,owner) VALUES (?,?,?,?)",
            (job_id, vote, now(), tenancy.user_id()),
        )


def list_votes() -> dict:
    """Jobs this account voted on, split into up/down, for the feedback learner."""
    with connect() as con:
        rows = con.execute(
            "SELECT id,title,company,source_id,description,vote FROM ("
            + _job_select()
            + ") WHERE vote IS NOT NULL AND vote != 0 ORDER BY id DESC",
            (tenancy.user_id(),),
        ).fetchall()
    return {
        "up": [row for row in rows if row["vote"] > 0],
        "down": [row for row in rows if row["vote"] < 0],
    }


def update_job_status(job_id: int, status: str) -> None:
    with connect() as con:
        _set_job_state(con, job_id, status=status)


def update_job_match(job_id: int, score: float, reason: str, profile_hash: str) -> None:
    with connect() as con:
        _set_job_state(
            con, job_id, match_score=score, match_reason=reason, profile_hash=profile_hash
        )
