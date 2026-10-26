"""SQLite persistence facade, grouped by owned tables."""

from .accounts import (  # noqa: F401
    active_owners_with_profile,
    add_profile_version,
    append_answer,
    get_feedback_profile,
    get_interview_state,
    get_preferences,
    get_profile,
    list_answers,
    list_profile_versions,
    set_feedback_profile,
    set_interview_state,
    set_preferences,
    set_profile,
)
from .connection import connect, now, stale_cutoff  # noqa: F401
from .documents import add_document, count_documents, get_document, list_documents  # noqa: F401
from .ingestion import (
    record_detail_fetch as record_detail_fetch,
)
from .ingestion import (
    touch_recent_detail as touch_recent_detail,
)
from .ingestion import upsert_job  # noqa: F401
from .job_state import (  # noqa: F401
    _set_job_state,
    get_job_travel,
    list_votes,
    set_job_travel,
    set_job_vote,
    set_rank_scores,
    update_job_match,
    update_job_signals,
    update_job_status,
)
from .jobs import (  # noqa: F401
    _disabled_user_sources,
    _job_select,
    count_jobs,
    count_jobs_by_source,
    effective_user_sources,
    get_job,
    job_date,
    list_jobs,
    list_jobs_for_matching,
    list_scored_jobs,
    normalize_posted_at,
    parse_job_date,
)
from .meta import load_token_df, meta_get, meta_set, save_token_df  # noqa: F401
from .recovery import recover_interrupted_work  # noqa: F401
from .schema import (  # noqa: F401
    SCHEMA,
    _backup_before_upgrade,
    _backup_database,
    _fill_search_text,
    _migrate_ownership,
    _normalize_job_dates,
    _recover_raw_verdicts,
    _search_text,
    init_db,
    migrate,
)
from .sessions import (  # noqa: F401
    add_message,
    add_session_usage,
    create_session,
    get_session,
    list_messages,
    list_sessions,
    update_session,
)
from .sources import (  # noqa: F401
    list_sources,
    list_user_sources,
    load_source_configs,
    save_source_config,
    set_source_config_enabled,
    set_user_source,
    sync_sources,
    update_source_status,
)
from .usage import daily_usage_cost, record_usage, usage_total  # noqa: F401
