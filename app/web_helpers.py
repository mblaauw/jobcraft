"""web_helpers responsibilities extracted without route changes."""

from __future__ import annotations

import difflib
import json
import logging
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from . import (
    config,
    profile,
    store,
    tenancy,
)

log = logging.getLogger("jobcraft")



def _model_label(model) -> str | None:
    if isinstance(model, dict):
        return f"{model.get('providerID')}/{model.get('id')}"
    return str(model) if model else None



def _persist_turn(session_id: str, result: dict, kind: str, *, hide_kickoff: bool = False) -> None:
    for message in result["messages"]:
        message_id = message.get("id") or f"anon-{uuid.uuid4()}"
        hidden = int(
            hide_kickoff
            and message["role"] == "user"
            and message["text"].strip() == config.KICKOFF_TEXT
        )
        store.add_message(
            session_id,
            message_id,
            message["role"],
            message["text"],
            _model_label(message.get("model")),
            hidden,
        )
    usage = result.get("usage") or {}
    if usage.get("cost_usd") or usage.get("input_tokens") or usage.get("output_tokens"):
        store.record_usage(
            session_id,
            kind,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            float(usage.get("cost_usd") or 0.0),
        )
    session = result.get("session") or {}
    if session:
        store.update_session(
            session_id,
            cost_usd=float(session.get("cost") or 0.0),
            tokens_in=int((session.get("tokens") or {}).get("input") or 0),
            tokens_out=int((session.get("tokens") or {}).get("output") or 0),
        )
    else:
        store.add_session_usage(
            session_id,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            float(usage.get("cost_usd") or 0.0),
        )



def _last_assistant_text(session_id: str) -> str:
    for row in reversed(store.list_messages(session_id)):
        if row["role"] == "assistant" and row["text"].strip():
            return row["text"].strip()
    return ""



def _visible_messages(session_id: str) -> list[dict]:
    rows = store.list_messages(session_id)
    visible = []
    for row in rows:
        if row["hidden"]:
            continue
        visible.append({"role": row["role"], "text": row["text"]})
    return visible



def _profile_meta() -> dict:
    exists = profile.profile_exists()
    updated = None
    if exists:
        updated = datetime.fromtimestamp(
            config.user_path(profile.PROFILE_FILE).stat().st_mtime, UTC
        ).isoformat()
    return {"exists": exists, "updated": updated}



def _workflow(
    profile_state: dict, jobs: dict, documents: int, draft: dict | None, queue: int
) -> dict:
    if not profile_state["exists"]:
        if tenancy.user_id() != tenancy.LEGACY_ID:
            return {
                "label": "Vertel wat voor werk je zoekt",
                "href": "/interview",
                "detail": "Een kort gesprek, één vraag tegelijk. Daaruit maken we je profiel.",
                "step": 1,
            }
        return {
            "label": "Maak je eerste profiel",
            "href": "/profile",
            "detail": "We lezen je bestaande materiaal in en stellen een profiel voor dat je zelf controleert.",
            "step": 1,
        }
    if draft:
        return {
            "label": "Bekijk het voorstel voor je profiel",
            "href": "/profile#bijwerken",
            "detail": "Je ziet precies wat er verandert en kiest zelf of je het overneemt.",
            "step": 1,
        }
    if jobs["total"] == 0:
        return {
            "label": "Haal de eerste vacatures op",
            "href": "/sources",
            "detail": "Kies bij Bronnen welke vacaturesites meedoen.",
            "step": 2,
        }
    if queue > 0:
        return {
            "label": f"{queue} kansrijke vacatures wachten op een beoordeling",
            "href": "/jobs",
            "detail": "Werk de beoordelingen bij op de vacaturelijst. Je krijgt per vacature een toelichting.",
            "step": 3,
        }
    if documents == 0:
        return {
            "label": "Kies een vacature en maak je CV",
            "href": "/jobs",
            "detail": "Open een vacature die je aanspreekt; Jobcraft past je CV of brief erop aan.",
            "step": 4,
        }
    return {
        "label": "Bekijk je nieuwste vacatures",
        "href": "/jobs",
        "detail": "Je profiel, beoordelingen en documenten zijn bijgewerkt.",
        "step": 4,
    }



def _profile_context(**extra) -> dict:
    draft_pair = profile.pending_draft()
    draft = draft_pair[1] if draft_pair else None
    current = profile.read_profile()
    diff = ""
    if draft:
        diff = "\n".join(
            difflib.unified_diff(
                current.splitlines(),
                draft["profile_md"].splitlines(),
                fromfile="huidige profile.md",
                tofile="voorstel",
                lineterm="",
            )
        )
    context = {
        "profile_md": current,
        "profile_sections": profile.split_sections(current),
        "preferences": profile.read_preferences(),
        "editor_chars": len(current),
        "editor_lines": (current.count("\n") + 1) if current else 0,
        "versions": store.list_profile_versions(20),
        "seed": profile.seed_status(),
        "bootstrap_status": store.meta_get("bootstrap_status", "idle"),
        "bootstrapped_at": store.meta_get("profile_bootstrapped_at"),
        "usage": store.usage_total(),
        "draft": draft,
        "draft_diff": diff,
        "draft_profile_text": draft["profile_md"] if draft else "",
        "draft_preferences_text": (
            json.dumps(draft["preferences"], indent=2, ensure_ascii=False) if draft else ""
        ),
        "new_answer_chars": len(profile.new_answer_log()),
        "consolidation_cost_note": profile.CONSOLIDATION_COST_NOTE,
        "private_note": "De privésectie wordt nooit naar documenten of matching gestuurd.",
    }
    context.update(extra)
    return context



def _jobs_return_target(url: str) -> str:
    """Keep list/detail context without permitting external or unrelated redirects."""
    if (
        url != url.strip()
        or url.startswith("//")
        or "\\" in url
        or any(ord(char) < 32 for char in url)
    ):
        return "/jobs"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "/jobs"
    valid_path = parts.path == "/jobs" or (
        parts.path.startswith("/jobs/") and parts.path.removeprefix("/jobs/").isdigit()
    )
    return url if not parts.scheme and not parts.netloc and valid_path else "/jobs"

