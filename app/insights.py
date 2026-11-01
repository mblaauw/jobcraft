"""Paid, on request only: skills gap and interview preparation per vacancy."""

from __future__ import annotations

import json

from . import profile, runtime, store
from .util import now_iso, parse_json_object
from .workflows import _budget_reached

KINDS = {"gap": "job_gap", "prep": "job_prep"}


def _key(kind: str, job_id: int) -> str:
    return f"insight:{kind}:{job_id}"


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
        return None
    result.update(content_hash=job["content_hash"], created_at=now_iso())
    store.meta_set(_key(kind, job["id"]), json.dumps(result, ensure_ascii=False))
    return result
