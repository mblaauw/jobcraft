"""routers pages responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)

from .. import (
    applications,
    auth,
    config,
    judgements,
    profile,
    ranking,
    sources,
    store,
    tasks,
    tenancy,
    today,
)
from ..auth.users import _list_users
from ..web import templates

log = logging.getLogger("jobcraft")

WORKER_STALE_SECONDS = 180

router = APIRouter()


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(auth.require_user)])
def dashboard(request: Request):
    """Today: what to do to get closer to an interview, what is new, and how the week goes."""
    if auth.require_user(request)["role"] == auth.ROLE_ADMIN:
        return RedirectResponse(url="/admin", status_code=303)
    if tenancy.user_id() != tenancy.LEGACY_ID and not profile.profile_exists():
        return RedirectResponse(url="/start", status_code=303)
    ranking.refresh_travel()  # the picks below hide what is too far, so their times must be current
    columns = applications.board()
    task_list = today.tasks(columns)
    start = today.start_state()
    judge_round = judgements.today_round() if start != "open" else None
    asked = judge_round["job"]["id"] if judge_round and judge_round["job"] else None
    matches, fresh = today.new_matches(6, today.last_visit())
    matches = [job for job in matches if job["id"] != asked][:5]  # not twice on one page
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "start": start,
            "judge_round": judge_round,
            "first_name": today.first_name(),
            "has_profile": profile.profile_exists(),
            "tasks": task_list,
            "summary": today.summary(task_list, fresh),
            "matches": matches,
            "fresh": fresh,
            "week": today.week(columns),
            "tip": today.tip(),
            "has_jobs": bool(store.count_jobs()["total"]),
        },
    )


def _worker_state() -> dict:
    """In the combined role the worker runs inside this process, without its own heartbeat."""
    if os.environ.get("APP_ROLE", "all") != "web":
        return {"separate": False}
    raw = store.meta_get("worker_heartbeat")
    try:
        seen = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        seen = None
    alive = bool(seen and (datetime.now(UTC) - seen).total_seconds() < WORKER_STALE_SECONDS)
    return {"separate": True, "alive": alive, "seen": raw}


def _last_scan(sources: list) -> str | None:
    """The last round that fetched something: from the health record, else from the sources."""
    try:
        health = json.loads(store.meta_get("source_health") or "{}")
    except ValueError:
        health = {}
    moments = [item["last_ok"] for item in health.values() if item.get("last_ok")]
    moments += [row["last_scan_at"] for row in sources if row["last_scan_at"] and row["last_status"] == "ok"]
    return max(moments, default=None)


def _top_gaps(jev_full: set[str] | frozenset = frozenset()) -> list[dict]:
    """Per account with a profile: how much of the top of its list still waits for Jev,
    and whether that account used up today's Jev calls (`jev_full` holds those owners).

    Account names are account data, not what the person does; a tenth of the top is noise.
    """
    names = {row["id"]: (row["display_name"] or row["username"]) for row in _list_users()}
    gaps = []
    for owner in store.active_owners_with_profile():
        with tenancy.scope({"id": owner}):
            gap = ranking.top_wait()
        if gap and gap["of"] and gap["waiting"] >= max(1, gap["of"] // 10):
            gaps.append({"name": names.get(owner, "een account"), **gap, "jev_full": owner in jev_full})
    return gaps


# Task kinds as an administrator reads them; an unknown kind shows its own name.
TASK_LABELS = {
    "scan": "Ophalen",
    "vacancy": "Vacaturetekst opmaken",
    "match": "Beoordelen",
    "verdict": "Beoordelen",
    "rerank": "Opnieuw rangschikken",
    "index": "Zoekindex bijwerken",
    "document": "Documenten maken",
    "consolidate": "Profiel opbouwen",
    "position": "Loopbaanpositie",
    "insight": "Inzichten",
    "digest": "Dagmail",
    "cv_import": "CV inlezen",
    "cv_coach": "CV-coach",
}


def _attention(health: dict, worker: dict, sources: list, gaps: list | None = None) -> list[dict]:
    """What an administrator should look at, each line linking to where it is fixed."""
    items: list[dict] = []
    if worker.get("separate") and not worker.get("alive"):
        items.append({"level": "warn", "text": "De worker draait niet: scans en beoordelingen wachten.", "href": None, "label": ""})
    names = {row["id"]: row["name"] for row in sources if row["enabled"]}
    for source_id, item in health.items():
        if source_id not in names:
            continue
        if item.get("failures"):
            rounds = item["failures"]
            reason = (item.get("reason") or "").split(":")[0][:60]
            text = f"{names[source_id]}: {'laatste ronde mislukt' if rounds == 1 else f'{rounds} rondes achter elkaar mislukt'}"
            items.append({"level": "warn", "text": text + (f", {reason}" if reason else ""), "href": "/sources", "label": "Bron"})
        elif item.get("dropped"):
            items.append({"level": "info", "text": f"{names[source_id]}: veel minder gevonden dan normaal", "href": "/sources", "label": "Bron"})
    for failure in tasks.failure_kinds():
        label = TASK_LABELS.get(failure["kind"], failure["kind"])
        times = "1 keer" if failure["n"] == 1 else f"{failure['n']} keer"
        if failure["interrupted"]:
            text = f"{label} {times} onderbroken door een herstart"
        else:
            text = f"{label} mislukte {times}" + (f": {failure['error']}" if failure["error"] else "")
        items.append({"level": "warn", "text": text, "href": "/admin/ai", "label": "AI"})
    for row in store.failed_attempts(days=1):
        if row["n"] >= 5:
            items.append({"level": "warn", "text": f"{row['n']} mislukte inlogpogingen op {row['names']} namen vanaf {row['ip'] or 'onbekend adres'}", "href": "/users/activiteit", "label": "Inlogactiviteit"})
    for gap in gaps or ():
        text = f"{gap['name']}: {gap['waiting']} van de {gap['of']} best gerangschikte vacatures wachten nog op een snelle beoordeling"
        if gap.get("jev_full"):
            text += ", want het Jev-plafond van vandaag is bereikt (weer ruimte na middernacht UTC)"
        items.append({"level": "info", "text": text, "href": "/admin/ai", "label": "AI"})
    return items


@router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(auth.require_admin)])
def admin_page(request: Request):
    """Does it run, who uses it, what does it cost: the administrator's home."""
    auth.require_admin(request)
    worker = _worker_state()
    try:
        health = json.loads(store.meta_get("source_health") or "{}")
    except ValueError:
        health = {}
    sources_rows = store.list_sources()
    runs = {
        **worker,
        "last_scan": _last_scan(sources_rows),
        "open": tasks.open_count(),
        # A fresh install has no round yet and is not down for it; the worker decides.
        "ok": not worker.get("separate") or bool(worker.get("alive")),
    }
    logins = store.auth_counts(days=1)
    people = {
        **store.account_counts(),
        "logins": logins.get("login_ok", 0) + logins.get("invite_used", 0),
        "failed": logins.get("login_failed", 0),
    }
    overview = store.usage_overview()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    today_row = next((row for row in overview["days"] if row["day"] == today), {"tokens": 0, "cost": 0.0})
    # Both limits hold per account and the token limit leaves Jev out: measure them that way.
    per_owner = store.usage_today_per_owner()
    token_limit = int(os.environ.get("AI_DAILY_TOKEN_LIMIT", "500000") or 0)
    jev_limit = int(os.environ.get("SYSTEMONE_DAILY_LIMIT", "80") or 0)
    busiest = max((row["tokens"] for row in per_owner), default=0)
    jev_full = {row["owner"] for row in per_owner if jev_limit and row["jev"] >= jev_limit}
    ai = {
        "tokens_today": sum(row["tokens"] for row in per_owner),
        "token_limit": token_limit,
        "share": round(busiest / token_limit * 100) if token_limit else 0,
        "priced": any(row["cost"] for row in overview["kinds"]),
        # Gateways that report no price book $0; say so rather than show a total that looks complete.
        "unpriced": any(row["calls"] and not row["cost"] for row in overview["kinds"]),
        "cost_today": float(today_row["cost"]),
        "cost_period": sum(float(row["cost"]) for row in overview["days"]),
        "jev_today": sum(row["jev"] for row in per_owner),
        "jev_full": bool(jev_full),
    }
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"runs": runs, "people": people, "ai": ai, "attention": _attention(health, worker, sources_rows, _top_gaps(jev_full))},
    )


@router.get("/admin/instellingen", response_class=HTMLResponse, dependencies=[Depends(auth.require_admin)])
def admin_settings(request: Request):
    """What this installation does and keeps; values from the environment are read-only here."""
    auth.require_admin(request)
    return templates.TemplateResponse(
        request,
        "admin_settings.html",
        {
            "schedule_enabled": store.meta_get("scan_schedule_enabled", "1") == "1",
            "schedule_times": store.meta_get("scan_schedule_times", os.environ.get("SCAN_TIMES", "07:30,17:30")),
            "schedule_last_fire": store.meta_get("scheduler_last_fire"),
            "smtp": bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM")),
            "digest_time": os.environ.get("DIGEST_TIME", "08:30"),
            "token_limit": int(os.environ.get("AI_DAILY_TOKEN_LIMIT", "500000") or 0),
            "budget": float(os.environ.get("MATCH_DAILY_BUDGET_USD", "0.50") or 0),
            "jev_limit": int(os.environ.get("SYSTEMONE_DAILY_LIMIT", "80") or 0),
            "ai_log_days": int(os.environ.get("AI_LOG_DAYS", "30") or 30),
            "auth_log_days": store.auth_events.retention_days(),
            "paths": {
                "data": str(config.DATA_DIR),
                "sources.yaml": str(sources.DATA_FILE),
                "database": str(config.DB_PATH),
            },
        },
    )


@router.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})
