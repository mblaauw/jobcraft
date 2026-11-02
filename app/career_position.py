"""Where someone stands: what recruiters will see, what to play up and what to fix first."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from . import application_plan, profile, store, tasks, tenancy
from .runtime import get_manager
from .util import now_iso

log = logging.getLogger("jobcraft")

KEY = "career_position"
STATUS = "career_position_status"
SAMPLE_JOBS = 14
STALE_SECONDS = 15 * 60


class Strength(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    proof: str
    how_to_use: str


class Weakness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    how_it_looks: str
    action: str


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str
    first_impression: str
    strengths: list[Strength]
    weaknesses: list[Weakness]
    in_demand_have: list[str]
    in_demand_missing: list[str]
    best_next_steps: list[str]


def _digest() -> str:
    return hashlib.sha256(profile.read_document_excerpt(12000).encode()).hexdigest()


def load() -> dict | None:
    try:
        data = json.loads(store.meta_get(KEY) or "null")
    except ValueError:
        return None
    if data:
        data["outdated"] = data.get("profile_digest") != _digest()
    return data


def status() -> str:
    """idle, running or error; a job that went silent long ago no longer counts as running."""
    try:
        data = json.loads(store.meta_get(STATUS) or "{}")
    except ValueError:
        return "idle"
    if data.get("state") == "running":
        started = datetime.fromisoformat(data["at"])
        if (datetime.now(UTC) - started).total_seconds() > STALE_SECONDS:
            return "idle"
    return data.get("state", "idle")


def _set_status(state: str) -> None:
    store.meta_set(STATUS, json.dumps({"state": state, "at": now_iso()}))


def request() -> None:
    _set_status("running")
    owner = tenancy.user_id()
    tasks.enqueue("position", owner=owner, dedupe_key=f"position:{owner}")


def cv_checks(markdown: str) -> list[dict]:
    """What any recruiter checks first, counted from the CV itself. Free and instant."""
    sections = {
        item["heading"]: item["text"] for item in profile.split_sections(markdown or "") if item["heading"]
    }
    work = next((text for heading, text in sections.items() if heading.lower().startswith(("werkervaring", "ervaring"))), "")
    bullets = [line for line in work.splitlines() if line.strip().startswith(("-", "*", "•"))]
    lines = [line for line in work.splitlines() if line.strip()]
    measured = [line for line in (bullets or lines) if re.search(r"\d", line)]
    skills = next((text for heading, text in sections.items() if heading.lower().startswith(("vaardigheden", "skills", "kern"))), "")
    skill_count = len([item for item in re.split(r"[,;\n•]", skills) if item.strip()])
    summary = next((text for heading, text in sections.items() if heading.lower().startswith(("over mij", "profiel", "samenvatting"))), "")
    checks = [
        {
            "label": "Resultaten met cijfers",
            "ok": len(measured) >= 3,
            "detail": f"{len(measured)} van je regels werkervaring noemen een getal. Cijfers zijn wat een recruiter onthoudt: mik op minstens 3.",
        },
        {
            "label": "Werkervaring met inhoud",
            "ok": len(bullets) >= 3,
            "detail": f"{len(bullets)} losse punten over wat je deed. Zonder punten blijft alleen je functietitel over, en die zegt weinig.",
        },
        {
            "label": "Vaardigheden als zoekwoorden",
            "ok": skill_count >= 6,
            "detail": f"{skill_count} vaardigheden. Cv-systemen zoeken op deze woorden; 6 tot 15 is goed.",
        },
        {
            "label": "Korte samenvatting bovenaan",
            "ok": 60 <= len(summary) <= 800,
            "detail": "De eerste plek waar iemand kijkt. Twee of drie zinnen over wat je doet en voor wie.",
        },
    ]
    return checks


def _market_sample() -> str:
    rows = store.list_jobs_page(SAMPLE_JOBS, 0, "new", sort="rank", within_travel=False)
    return "\n\n".join(
        f"### {row['title']} bij {row['company'] or 'onbekend'}\n{(row['description'] or '')[:700]}" for row in rows
    )


def make(mgr) -> dict:
    result = application_plan.call_json(
        mgr,
        f"## Profiel\n{application_plan.person_source()}\n\n"
        f"## Vacatures die bij deze persoon passen\n{_market_sample() or '(nog geen)'}\n\n"
        "Geef JSON met: headline (één zin: zo kom je over), first_impression (wat een recruiter in zes "
        "seconden denkt), strengths (3 of 4, elk {title, proof, how_to_use}), weaknesses (2 tot 4, elk "
        "{title, how_it_looks, action}), in_demand_have (wat de vacatures vragen en jij hebt, hoogstens 8), "
        "in_demand_missing (wat ze vaak vragen en jij mist, hoogstens 6), best_next_steps (de 3 dingen "
        "die deze week het meeste opleveren).",
        agent="career_coach",
        model=Position,
        ref="position",
    )
    result.update(created_at=now_iso(), profile_digest=_digest())
    store.meta_set(KEY, json.dumps(result, ensure_ascii=False))
    return result


@tasks.handler("position")
def _task(task):
    from . import auth

    user = auth._get_user_by_id(task["owner"]) if task["owner"] else None
    if user is None:
        raise RuntimeError("unknown task owner")
    with tenancy.scope(
        {"id": user["id"], "username": user["username"], "display_name": user["display_name"], "role": user["role"]}
    ):
        try:
            make(get_manager())
            _set_status("idle")
        except Exception:
            log.exception("career position failed")
            _set_status("error")
    return {}
