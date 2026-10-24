"""workflows responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime

from . import (
    auth,
    config,
    docs,
    feedback,
    matching,
    profile,
    ranking,
    runtime,
    sources,
    store,
    tasks,
    tenancy,
)
from .runtime import get_manager

log = logging.getLogger("jobcraft")

_document_lock = threading.Lock()


def _run_scan() -> dict:
    store.meta_set("scan_status", "running")
    try:
        summary = sources.scan()
        feedback.refresh_document_frequency()
        store.meta_set("scan_summary", json.dumps(summary, ensure_ascii=False))
        store.meta_set("scan_status", "done")
        if os.environ.get("MATCH_AFTER_SCAN", "1") != "0":
            for owner in store.active_owners_with_profile():
                tasks.enqueue("match", owner=owner, dedupe_key=f"match:{owner}")
        return summary
    except Exception as exc:  # noqa: BLE001 - surface failures in the UI
        log.exception("scan failed")
        store.meta_set("scan_status", f"error: {exc}"[:400])
        raise


def _run_match() -> None:
    try:
        summary = matching.match_jobs(runtime.manager)
        summary["finished_at"] = datetime.now(UTC).isoformat()
        store.meta_set("match_summary", json.dumps(summary, ensure_ascii=False))
        store.meta_set("match_status", "done")
    except Exception as exc:  # noqa: BLE001 - surface failures in the UI
        log.exception("matching failed")
        store.meta_set("match_status", f"error: {exc}"[:400])


def _doc_status(job_id: int) -> dict:
    raw = store.meta_get(f"doc_status:{job_id}")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _run_document(job_id: int, kind: str) -> None:
    key = f"doc_status:{job_id}"
    try:
        job = store.get_job(job_id)
        if not job:
            raise RuntimeError("vacature niet gevonden")
        mgr = get_manager()
        result = docs.generate_for_job(mgr, job, kind)
        store.meta_set(
            key,
            json.dumps(
                {
                    "state": "done",
                    "kind": kind,
                    "version": result.get("version"),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001 - surface failures in the UI
        log.exception("document generation failed")
        message = str(exc)
        if "provider.auth" in message or "api_key_expired" in message:
            message = "API-key verlopen of ongeldig. Werk LLM_API_KEY bij en herstart de app."
        store.meta_set(key, json.dumps({"state": "error", "kind": kind, "error": message[:300]}))


def _run_bootstrap() -> None:
    try:
        mgr = get_manager()
        full = store.meta_get("consolidation_full", "0") == "1"
        incremental_ready = (
            profile.profile_exists() and store.meta_get("answer_log_offset") is not None
        )
        effective_full = full or not incremental_ready
        prompt = profile.build_consolidation_prompt(full=effective_full)
        text, usage = mgr.generate(prompt, agent=config.AGENT_WRITER)
        store.record_usage(
            None,
            "consolidation",
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            float(usage.get("cost_usd") or 0.0),
            agent="writer",
        )
        try:
            data = profile.parse_consolidation(text)
        except Exception as exc:
            saved = profile.save_raw_consolidation(text)
            raise RuntimeError(f"{exc}; raw reply kept at {saved}") from exc
        draft = profile.save_draft(
            data["profile_md"],
            data["preferences"],
            {"mode": "full" if effective_full else "incremental", "usage": usage},
        )
        store.meta_set("consolidation_draft", str(draft))
        store.meta_set("bootstrap_status", "ready")
        log.info("consolidation draft ready: %s", draft)
    except Exception as exc:  # noqa: BLE001 - surface failures in the UI
        log.exception("consolidation failed")
        message = str(exc)
        if "provider.auth" in message or "api_key_expired" in message:
            message = "API-key verlopen of ongeldig. Werk LLM_API_KEY bij en herstart de app."
        store.meta_set("bootstrap_status", f"error: {message}"[:500])


@tasks.handler("rerank")
def _task_rerank(task):
    user = auth._get_user_by_id(task["owner"])
    if user is None:
        raise RuntimeError("unknown task owner")
    with tenancy.scope(
        {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    ):
        ranking.rerank_all()


@tasks.handler("match")
def _task_match(task):
    user = auth._get_user_by_id(task["owner"])
    if user is None:
        raise RuntimeError("unknown task owner")
    with tenancy.scope(
        {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    ):
        budget = float(os.environ.get("MATCH_DAILY_BUDGET_USD", "0.50") or 0)
        if budget > 0 and store.daily_usage_cost() >= budget:
            store.meta_set("match_status", "error: dagbudget bereikt")
            return
        _run_match()


@tasks.handler("consolidate")
def _task_consolidate(task):
    user = auth._get_user_by_id(task["owner"])
    if user is None:
        raise RuntimeError("unknown task owner")
    with tenancy.scope(
        {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    ):
        _run_bootstrap()


@tasks.handler("document")
def _task_document(task):
    user = auth._get_user_by_id(task["owner"])
    if user is None:
        raise RuntimeError("unknown task owner")
    payload = json.loads(task["payload_json"])
    with tenancy.scope(
        {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    ):
        _run_document(int(payload["job_id"]), str(payload["kind"]))


@tasks.handler("scan")
def _task_scan(task):
    if task["owner"] is not None:
        raise RuntimeError("scan tasks must be shared")
    return _run_scan()
