"""SQLite persistence facade, grouped by owned tables."""

from .accounts import (  # noqa: F401
    account_counts,
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
from .auth_events import (  # noqa: F401
    auth_counts,
    failed_attempts,
    failed_per_user,
    last_logins,
    list_auth_events,
    prune_auth_events,
    record_auth_event,
)
from .connection import connect, now, stale_cutoff  # noqa: F401
from .documents import (  # noqa: F401
    add_document,
    count_documents,
    delete_document,
    get_document,
    list_documents,
)
from .ingestion import fetched_within, known_job_urls, source_urls_seen_longest_ago, upsert_job  # noqa: F401
from .ingestion import (
    record_detail_fetch as record_detail_fetch,
)
from .ingestion import (
    touch_recent_detail as touch_recent_detail,
)
from .job_events import list_job_events, record_job_event, record_routes, status_funnel  # noqa: F401
from .job_facts import delete_job_facts, get_job_facts, load_job_facts, save_job_facts  # noqa: F401
from .job_list_filters import full_text_share_by_source  # noqa: F401
from .job_state import (  # noqa: F401
    _set_job_state,
    get_job_travel,
    light_job_states,
    list_votes,
    set_job_travel,
    set_job_travels,
    set_job_vote,
    set_rank_scores,
    update_job_match,
    update_job_signals,
    update_job_status,
)
from .jobs import (  # noqa: F401
    MATCHING_LIMIT,
    TRAVEL_FIELDS,
    _disabled_user_sources,
    _job_select,
    count_catalogue,
    count_jobs,
    count_jobs_by_source,
    effective_user_sources,
    get_job,
    has_jobs,
    import_source,
    job_date,
    job_status_counts,
    jobs_missing_travel,
    list_jobs,
    list_jobs_for_matching,
    list_jobs_page,
    list_scored_jobs,
    normalize_posted_at,
    parse_job_date,
    travel_key,
)
from .meta import (  # noqa: F401
    common_tokens,
    count_event,
    load_token_df,
    meta_get,
    meta_like,
    meta_set,
    save_token_df,
)
from .reach import reach_counts as reach_counts
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
from .usage import (  # noqa: F401
    daily_usage_calls,
    daily_usage_cost,
    daily_usage_tokens,
    record_usage,
    usage_overview,
    usage_today_per_owner,
    usage_total,
)
