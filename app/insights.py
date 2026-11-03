"""Paid, on request only: skills gap and interview preparation per vacancy.

Both run in the background, so nobody stares at a page that seems to hang.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from . import config, profile, runtime, store, tasks, tenancy
from .ai_budget import budget_reached as _budget_reached
from .util import now_iso, parse_json_object

log = logging.getLogger("jobcraft")

KINDS = {"gap": "job_gap", "prep": "job_prep"}
STATUS_PREFIX = "insight_status:"
STALE_SECONDS = 10 * 60
PROBLEMS = {
    "ai": "De AI-dienst is nu niet beschikbaar. Probeer het later opnieuw.",
    "budget": "Je daglimiet voor AI is bereikt. Morgen kan het weer.",
    "failed": "Dit lukte niet. Probeer het opnieuw.",
}


def _key(kind: str, job_id: int) -> str:
    return f"insight:{kind}:{job_id}"


def _status_key(kind: str, job_id: int) -> str:
    return f"{STATUS_PREFIX}{kind}:{job_id}"


def _set_status(kind: str, job_id: int, state: str, **extra) -> None:
    stamp = datetime.now(UTC).isoformat()
    store.meta_set(_status_key(kind, job_id), json.dumps({"state": state, "updated_at": stamp, **extra}))


def status(kind: str, job_id: int) -> dict:
    """running, done or error; a run that went silent counts as failed."""
    try:
        data = json.loads(store.meta_get(_status_key(kind, job_id)) or "{}")
    except ValueError:
        return {}
    if data.get("state") == "running":
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(data.get("updated_at") or "")).total_seconds()
        except ValueError:
            age = 0
        if age > STALE_SECONDS:
            return {"state": "error"}
    return data


def request(kind: str, job) -> str:
    """Empty when queued or already there; otherwise a key of PROBLEMS."""
    if load(kind, job) is not None or status(kind, job["id"]).get("state") == "running":
        return ""
    if not config.LLM_API_KEY and getattr(runtime.manager, "client", None) is None:
        return "ai"
    if _budget_reached():
        return "budget"
    _set_status(kind, job["id"], "running")
    owner = tenancy.user_id()
    tasks.enqueue(
        "insight",
        owner=owner,
        payload={"job_id": job["id"], "kind": kind},
        dedupe_key=f"insight:{owner}:{kind}:{job['id']}",
    )
    return ""


def context(job, problem: str = "") -> dict:
    """Everything the job page needs: results, what is running and what went wrong."""
    mark_seen(job["id"])
    found = {kind: load(kind, job) for kind in KINDS}
    states = {kind: ({"state": "done"} if found[kind] else status(kind, job["id"])) for kind in KINDS}
    if not problem and any(item.get("state") == "error" for item in states.values()):
        problem = "failed"
    return {**found, "insight_state": states, "insight_problem": PROBLEMS.get(problem, PROBLEMS["failed"] if problem else "")}


def mark_seen(job_id: int) -> None:
    for kind in KINDS:
        data = status(kind, job_id)
        if data.get("state") == "done" and not data.get("seen"):
            store.meta_set(_status_key(kind, job_id), json.dumps({**data, "seen": True}))


def load(kind: str, job) -> dict | None:
    """A saved result stays valid until the vacancy text changes."""
    raw = store.meta_get(_key(kind, job["id"]))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if data.get("content_hash") == job["content_hash"] else None


def _clean(kind: str, data: dict) -> dict:
    if kind == "gap":
        missing = [
            {"requirement": str(item.get("requirement") or ""), "how": str(item.get("how") or "")}
            for item in data.get("missing") or []
            if isinstance(item, dict) and item.get("requirement")
        ]
        return {"have": [str(item) for item in data.get("have") or []][:8], "missing": missing[:6]}
    questions = [
        {"question": str(item.get("question") or ""), "angle": str(item.get("angle") or "")}
        for item in data.get("questions") or []
        if isinstance(item, dict) and item.get("question")
    ]
    return {"questions": questions[:6], "ask_them": [str(i) for i in data.get("ask_them") or []][:3]}


def generate(kind: str, job) -> dict | None:
    """Returns None when the AI is unavailable, the budget is spent or the answer is unusable."""
    manager = runtime.manager
    if kind not in KINDS or manager is None or manager.client is None or _budget_reached():
        return None
    prompt = (
        "## Profiel\n"
        f"{profile.read_public_excerpt(6000) or '(nog leeg)'}\n\n"
        "## Vacature\n"
        f"Titel: {job['title']}\nOrganisatie: {job['company'] or 'onbekend'}\n\n"
        f"{(job['description'] or '')[:8000]}\n\nAntwoord met alleen de gevraagde JSON."
    )
    try:
        text, usage = manager.generate(prompt, agent=KINDS[kind])
        store.record_usage(
            None,
            "insight",
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            float(usage.get("cost_usd") or 0.0),
            agent=KINDS[kind],
        )
        result = _clean(kind, parse_json_object(text))
    except Exception:
        log.exception("insight %s failed", kind)
        return None
    result.update(content_hash=job["content_hash"], created_at=now_iso())
    store.meta_set(_key(kind, job["id"]), json.dumps(result, ensure_ascii=False))
    return result


@tasks.handler("insight")
def _task(task):
    from . import auth

    user = auth._get_user_by_id(task["owner"]) if task["owner"] else None
    if user is None:
        raise RuntimeError("unknown task owner")
    payload = json.loads(task["payload_json"])
    kind, job_id = str(payload["kind"]), int(payload["job_id"])
    with tenancy.scope(
        {"id": user["id"], "username": user["username"], "display_name": user["display_name"], "role": user["role"]}
    ):
        job = store.get_job(job_id)
        result = generate(kind, job) if job else None
        if result is None:
            _set_status(kind, job_id, "error")
        else:
            _set_status(kind, job_id, "done", finished_at=datetime.now(UTC).isoformat())
    return {}
