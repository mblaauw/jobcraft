"""Daily summary: in the app always, by email only when the operator set up SMTP."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from html import escape

from . import ranking, store, tenancy
from .util import now_local


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def settings() -> dict:
    return {
        "email": store.meta_get("digest_email") or "",
        "enabled": store.meta_get("digest_enabled") == "1",
    }


def top_jobs(limit: int = 5) -> list[dict]:
    """Best new vacancies since the last email, with no AI call."""
    since = store.meta_get("digest_last_sent") or ""
    rows = store.list_jobs_page(
        50, 0, "new", sort="rank", within_travel=True, only_unvoted=True
    )
    fresh = [row for row in rows if (row["first_seen"] or "") > since]
    return fresh[:limit]


def compose(jobs: list[dict], to_address: str) -> EmailMessage:
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    message = EmailMessage()
    message["Subject"] = f"{len(jobs)} nieuwe vacatures voor je"
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = to_address
    lines = [
        f"- {job['title']} · {job['company'] or 'Onbekende organisatie'}\n  {base}/jobs/{job['id']}"
        for job in jobs
    ]
    message.set_content("Dit zijn je beste nieuwe vacatures:\n\n" + "\n".join(lines))
    items = "".join(
        f'<li><a href="{escape(base, quote=True)}/jobs/{job["id"]}">{escape(job["title"] or "")}</a>'
        f" · {escape(job['company'] or 'Onbekende organisatie')}</li>"
        for job in jobs
    )
    message.add_alternative(
        f"<p>Dit zijn je beste nieuwe vacatures:</p><ul>{items}</ul>", subtype="html"
    )
    return message


def send_if_due() -> str:
    """Runs inside the owner's scope; never sends twice a day or an empty message."""
    today = now_local().date().isoformat()
    config = settings()
    if not (config["enabled"] and config["email"] and smtp_configured()):
        return "off"
    if store.meta_get("digest_last_date") == today:
        return "already"
    ranking.refresh_travel()
    jobs = top_jobs()
    store.meta_set("digest_last_date", today)
    if not jobs:
        return "empty"
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(compose(jobs, config["email"]))
    store.meta_set("digest_last_sent", max(job["first_seen"] or "" for job in jobs))
    store.count_event("digests_sent")
    return f"sent:{tenancy.user_id()}"
