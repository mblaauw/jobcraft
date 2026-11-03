"""workflows responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime

from . import (
    application_plan,
    auth,
    career_position,  # noqa: F401  (registers its task)
    config,
    digest,
    document_pipeline,
    feedback,
    insights,  # noqa: F401  (registers its task)
    job_index,
    matching,
    notifications,
    profile,
    ranking,
    runtime,
    sources,
    store,
    tasks,
    tenancy,
    vacancy_reader,  # noqa: F401  (registers its task)
)
from .ai_budget import budget_reached as _budget_reached
from .runtime import get_manager

log = logging.getLogger("jobcraft")

_document_lock = threading.Lock()


def _run_scan() -> dict:
    store.meta_set("scan_status", "running")
    try:
        summary = sources.scan()
        feedback.refresh_document_frequency()
        _refresh_index()
        store.meta_set("scan_summary", json.dumps(summary, ensure_ascii=False))
        store.meta_set("scan_status", "done")
        if os.environ.get("MATCH_AFTER_SCAN", "1") != "0":
            for owner in store.active_owners_with_profile():
                tasks.enqueue("match", owner=owner, dedupe_key=f"match:{owner}")
        return summary
    except Exception as exc:
        log.exception("scan failed")
        store.meta_set("scan_status", f"error: {exc}"[:400])
        raise


def _refresh_index() -> int:
    """New vacancies get their meaning vector before anyone's ranking runs; never fails a scan."""
    try:
        return job_index.refresh()
    except Exception:
        log.exception("vacancy index failed")
        return 0


@tasks.handler("index")
def _task_index(task):
    """At worker start: build or complete the index, then re-rank everyone with it."""
    if task["owner"] is not None:
        raise RuntimeError("index tasks must be shared")
    if _refresh_index():
        for owner in store.active_owners_with_profile():
            tasks.enqueue("rerank", owner=owner, dedupe_key=f"rerank:{owner}")
    return {}


def _refresh_notifications() -> None:
    """Assessments are already saved here, so a notification problem must not fail them."""
    try:
        notifications.refresh()
    except Exception:
        log.exception("match notifications failed")


def _run_match() -> None:
    """Work through the queue in a few rounds, stopping at the budget or without progress."""
    store.meta_set("match_status", "running")
    try:
        summary: dict = {}
        status = "done"
        for _ in range(max(1, int(os.environ.get("MATCH_AUTO_ROUNDS", "3")))):
            if _budget_reached():
                status = "budget"
                break
            current = matching.match_jobs(runtime.manager)
            for key in ("matched", "failed", "signals", "escalated", "cost_usd"):
                summary[key] = summary.get(key, 0) + current.get(key, 0)
            summary["errors"] = [*summary.get("errors", []), *current.get("errors", [])]
            summary["remaining"] = current.get("remaining", 0)
            if not (current.get("matched") or current.get("signals")):
                break
        if summary:
            summary["finished_at"] = datetime.now(UTC).isoformat()
            store.meta_set("match_summary", json.dumps(summary, ensure_ascii=False))
            if summary.get("failed") and not summary.get("matched"):
                status = f"error: {summary['failed']} beoordelingen mislukt"
        store.meta_set("match_status", status)
        _refresh_notifications()
    except Exception as exc:
        log.exception("matching failed")
        store.meta_set("match_status", f"error: {exc}"[:400])


DOC_STALE_SECONDS = 15 * 60


def _doc_status(job_id: int) -> dict:
    """A task that went silent long ago must not lock a page forever."""
    raw = store.meta_get(f"doc_status:{job_id}")
    if not raw:
        return {}
    try:
        status = json.loads(raw)
    except ValueError:
        return {}
    if status.get("state") == "running":
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(status.get("updated_at") or "")).total_seconds()
        except ValueError:
            age = 0
        if age > DOC_STALE_SECONDS:
            return {"state": "error", "error": "Dit duurde te lang. Probeer het opnieuw."}
    return status


def _run_document(job_id: int, kind: str) -> None:
    """kind: plan, both, resume or motivatie. The stage tells the page where the work is."""
    key = f"doc_status:{job_id}"
    started = _doc_status(job_id).get("started_at") or datetime.now(UTC).isoformat()

    def emit(stage: str) -> None:
        store.meta_set(
            key,
            json.dumps(
                {
                    "state": "running",
                    "kind": kind,
                    "stage": stage,
                    "started_at": started,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ),
        )

    try:
        job = store.get_job(job_id)
        if not job:
            raise RuntimeError("vacature niet gevonden")
        job = dict(job)
        mgr = get_manager()
        version = None
        if kind == "plan":
            application_plan.make(mgr, job, emit)
        elif kind.startswith("tone:"):
            _tone, tone, document_id = kind.split(":")
            row = store.get_document(int(document_id))
            if not row or row["job_id"] != job_id:
                raise RuntimeError("document niet gevonden")
            meta = json.loads(row["meta_json"])
            emit("write")
            version = document_pipeline.retone(mgr, job, row["kind"], meta["content"], meta, tone)["version"]
        else:
            version = document_pipeline.create_all(
                mgr, job, ("resume", "motivatie") if kind == "both" else (kind,), emit
            )
        store.meta_set(
            key,
            json.dumps(
                {
                    "state": "done",
                    "kind": kind,
                    "version": version,
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
    except Exception as exc:
        log.exception("document generation failed")
        message = "Er ging iets mis bij het schrijven. Probeer het opnieuw."
        if "provider.auth" in str(exc) or "api_key_expired" in str(exc):
            message = "De AI-dienst is nu niet bereikbaar. Probeer het later opnieuw."
        store.meta_set(key, json.dumps({"state": "error", "kind": kind, "error": message}))


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
    except Exception as exc:
        log.exception("consolidation failed")
        message = str(exc)
        if "provider.auth" in message or "api_key_expired" in message:
            message = "API-key verlopen of ongeldig. Werk LLM_API_KEY bij en herstart de app."
        store.meta_set("bootstrap_status", f"error: {message}"[:500])


@tasks.handler("digest")
def _task_digest(task):
    return {"result": digest.send_if_due()}


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
