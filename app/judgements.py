"""Quick judgements: the person's own "past wel / past niet", the best yardstick we have.

Every answer is a vote, which the ranker applies at once and for free. Five a day are
asked on Vandaag, chosen where an answer teaches the most: where Jev is unsure, where Jev
and the matcher disagree, plus the best match as a check. A "nee" may carry one reason;
reasons stay on disk and never go into a prompt.
"""

from __future__ import annotations

import json

from . import feedback, profile, ranking, store, tasks, tenancy, vacancy_reader, vacancy_text
from .scoring.digests import current_digest
from .util import now_iso, now_local

ROUND_KEY = "judge_round"
ROUND_SIZE = 5
POOL = 300  # the best this many unjudged vacancies within reach
UNSURE_FROM = 150  # unsure ones only from the top: an answer on a poor match teaches little
MAX_SKIPPED = 500
ANSWERS = {"ja": 1, "nee": -1, "twijfel": 0}
REASONS = {
    "soort": "Ander soort werk",
    "ver": "Te ver weg",
    "uren": "Uren passen niet",
    "eisen": "Eisen passen niet",
    "zwaar": "Te zwaar of ongezond",
    "salaris": "Salaris",
}


def _reason_key(job_id: int) -> str:
    return f"judgement:{job_id}"


def _record(job_id: int) -> dict:
    try:
        data = json.loads(store.meta_get(_reason_key(job_id)) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def reason_of(job_id: int) -> str:
    return str(_record(job_id).get("reason") or "")


def set_reason(job_id: int, reason: str) -> bool:
    """Only for a vacancy this person turned down, and only a reason we offer."""
    job = store.get_job(job_id)
    if reason not in REASONS or not job or not ((job["vote"] or 0) < 0 or job["status"] == "ignored"):
        return False
    store.meta_set(_reason_key(job_id), json.dumps({**_record(job_id), "reason": reason, "reason_at": now_iso()}))
    state = _state()
    if state.get("last_no") == job_id:
        store.meta_set(ROUND_KEY, json.dumps({**state, "last_no": None}))
    return True


def learn(job_id: int) -> None:
    """What every vote does: re-rank now, and the full pass in the background."""
    feedback.rebuild()
    ranking.after_vote(job_id)
    owner = tenancy.user_id()
    tasks.enqueue("rerank", owner=owner, dedupe_key=f"rerank:{owner}")


# ------------------------------------------------------------------ the daily round


def _state() -> dict:
    try:
        data = json.loads(store.meta_get(ROUND_KEY) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _disagree(job, digest: str) -> float:
    """How far Jev and a fresh matcher verdict lie apart, when they land on different sides."""
    signals = ranking.signals_of(job) or {}
    if signals.get("fit") is None or not ranking.verdict_is_fresh(job, digest):
        return 0.0
    fit, verdict = float(signals["fit"]), float(job["match_score"])
    return abs(fit - verdict) if (fit > 50) != (verdict >= 50) else 0.0


def pick(skipped: set[int]) -> list[int]:
    """The best match, the two Jev is least sure of, the two it most disagrees on; then the best."""
    rows = [
        row
        for row in store.list_jobs_page(POOL, 0, "new", sort="rank", within_travel=True, only_unvoted=True)
        if row["id"] not in skipped
    ]
    digest = current_digest()
    chosen: list = []
    companies: set[str] = set()

    def take(candidates, amount: int) -> None:
        for row in candidates:
            if len(chosen) >= amount:
                return
            company = (row["company"] or "").casefold()
            if row in chosen or (company and company in companies):
                continue
            companies.add(company)
            chosen.append(row)

    take(rows[:1], 1)
    # "Unsure" is what the route says, so this gate and the matcher's agree.
    prefs, data = profile.read_preferences(), feedback.load()
    unsure = [row for row in rows[:UNSURE_FROM] if ranking.route_of(row, digest, prefs, data).outcome == "escalate"]
    take(sorted(unsure, key=lambda row: (ranking.signals_of(row) or {}).get("confidence") or 0.0), 3)
    split = sorted((row for row in rows if _disagree(row, digest)), key=lambda row: -_disagree(row, digest))
    take(split, 5)
    take(rows, ROUND_SIZE)
    return [row["id"] for row in chosen]


def blurb(job) -> str:
    """A few lines to decide on: the tidy summary, else what the job is; never page clutter."""
    reading = vacancy_reader.usable(job)
    if reading and reading.get("summary"):
        return str(reading["summary"])
    for section in vacancy_text.structure(job["description"] or "")["sections"]:
        if section["kind"] in ("role", "about") and section["paragraphs"]:
            return section["paragraphs"][0]
    return ""


def today_round() -> dict:
    """Today's round, picked on the first visit of the day: the current vacancy and progress."""
    state = _state()
    today = now_local().date().isoformat()
    if state.get("date") != today:
        skipped = set(state.get("skipped") or [])
        state = {
            "date": today,
            "ids": pick(skipped),
            "done": 0,
            "last_no": None,
            "skipped": (state.get("skipped") or [])[-MAX_SKIPPED:],
        }
        store.meta_set(ROUND_KEY, json.dumps(state))
    open_jobs = [
        found
        for job_id in state["ids"]
        if (found := store.get_job(job_id)) and found["status"] == "new" and not found["vote"]
    ]  # a vacancy judged elsewhere in the meantime drops out
    last_no = store.get_job(state["last_no"]) if state.get("last_no") else None
    if last_no is not None and reason_of(last_no["id"]):
        last_no = None
    job = dict(open_jobs[0]) if open_jobs else None
    if job:
        job["travel"] = ranking.travel_of(job)
        job["blurb"] = blurb(job)
    return {
        "job": job,
        "position": state["done"] + 1,
        "size": state["done"] + len(open_jobs),
        "done": state["done"],
        "last_no": last_no,
    }


def answer(job_id: int, given: str) -> None:
    """A "ja" or "nee" is a vote; "weet ik niet" only moves on and is not asked again."""
    if given not in ANSWERS:
        raise ValueError("unknown answer")
    state = _state()
    vote = ANSWERS[given]
    job = store.get_job(job_id)
    # The rank before the vote moves it: measured against the answer, it says how good the list was.
    store.meta_set(_reason_key(job_id), json.dumps({
        "answer": given,
        "asked": job_id in (state.get("ids") or []),
        "rank_before": job["rank_score"] if job else None,
        "at": now_iso(),
    }))
    if vote:
        store.set_job_vote(job_id, vote)
        learn(job_id)
    else:
        state["skipped"] = [*(state.get("skipped") or []), job_id][-MAX_SKIPPED:]
    state["last_no"] = job_id if given == "nee" else None
    if job_id in (state.get("ids") or []):
        state["ids"].remove(job_id)
        state["done"] = int(state.get("done") or 0) + 1
    store.meta_set(ROUND_KEY, json.dumps(state))
