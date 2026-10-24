"""runtime responsibilities extracted without route changes."""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from . import (
    auth,
    config,
    docs,
    feedback,
    interview_state,
    llm,
    profile,
    runtime_lock,
    scheduler,
    sources,
    store,
    tasks,
)

log = logging.getLogger("jobcraft")

manager: llm.ServerManager | None = None
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global manager
    with runtime_lock.exclusive_data_dir(config.DATA_DIR):
        manager = None
        try:
            store.init_db()
            if store.load_token_df() is None:
                feedback.refresh_document_frequency()
            auth.init_auth()
            profile.ensure_dirs()
            interview_state.ensure_state()
            sources.ensure_sources_file()
            store.sync_sources(sources.load_sources())
            try:
                if not docs.CV_TEMPLATE.exists():
                    docs.ensure_templates()
            except Exception as exc:
                log.warning("document templates unavailable: %s", exc)
            logging.getLogger("httpx").setLevel(logging.WARNING)
            store.recover_interrupted_work()
            tasks.recover_interrupted()
            if not config.APP_SKIP_LLM:
                manager = llm.ServerManager()
                manager.start()
                if manager.client is None:
                    log.warning("LLM features disabled: set LLM_BASE_URL and LLM_API_KEY")
                else:
                    log.info("LLM client ready for %s", manager.base_url)
            tasks.start_workers(
                int(os.environ.get("TASK_WORKERS", "2")), resolve_owner=_resolve_task_owner
            )
            scheduler.start()
            yield
        finally:
            scheduler.stop()
            tasks.stop_workers()
            if manager:
                manager.stop()



def _resolve_task_owner(owner: str) -> dict | None:
    user = auth._get_user_by_id(owner)
    return auth._public_user(user) if user is not None and user["active"] else None



def get_manager() -> llm.ServerManager:
    if manager is None or manager.client is None:
        raise HTTPException(
            status_code=503, detail="LLM is niet geconfigureerd (LLM_BASE_URL/LLM_API_KEY)."
        )
    return manager



def _session_lock(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        return _session_locks.setdefault(session_id, threading.Lock())

