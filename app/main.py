"""Application composition: runtime, middleware and route groups."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import (  # noqa: F401
    auth,
    config,
    docs,
    feedback,
    geo,
    interview_state,
    llm,
    matching,
    profile,
    ranking,
    runtime_lock,
    scheduler,
    sources,
    store,
    systemone,
    tasks,
    tenancy,
)
from .routers import account, interview, jobs, pages
from .routers import profile as profile_routes
from .routers import sources as source_routes
from .routers import tasks as task_routes
from .routers.account import account_cv, account_cv_cancel, account_cv_confirm  # noqa: F401
from .routers.interview import (  # noqa: F401
    SendBody,
    api_messages,
    api_send,
    api_sessions,
    interview_new,
    interview_page,
    interview_toggle,
)
from .routers.jobs import (  # noqa: F401
    download_document,
    job_detail,
    job_document,
    job_status,
    job_vote,
    jobs_match_route,
    jobs_page,
)
from .routers.pages import admin_page, dashboard, healthz  # noqa: F401
from .routers.profile import (  # noqa: F401
    profile_accept,
    profile_consolidate,
    profile_discard,
    profile_home,
    profile_page,
    profile_restore,
    profile_save,
    profile_save_draft,
)
from .routers.sources import source_preference, source_toggle, sources_page, sources_scan  # noqa: F401
from .routers.tasks import api_task, api_tasks  # noqa: F401
from .runtime import _resolve_task_owner, _session_lock, get_manager, lifespan  # noqa: F401
from .web import templates  # noqa: F401
from .web_helpers import (  # noqa: F401
    _jobs_return_target,
    _last_assistant_text,
    _model_label,
    _persist_turn,
    _profile_context,
    _profile_meta,
    _visible_messages,
    _workflow,
)
from .workflows import (  # noqa: F401
    _doc_status,
    _run_bootstrap,
    _run_document,
    _run_match,
    _run_scan,
    _task_consolidate,
    _task_document,
    _task_match,
    _task_rerank,
    _task_scan,
)

app = FastAPI(title="Jobcraft", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
app.middleware("http")(auth.middleware)
app.include_router(auth.router)
for group in (pages, interview, source_routes, jobs, profile_routes, account, task_routes):
    app.include_router(group.router)
