"""From plan to finished document: write, check, have a recruiter read along, lay out."""

from __future__ import annotations

import io
import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from . import (
    application_plan,
    config,
    docs,
    document_contact,
    document_design,
    document_letter,
    standards,
    store,
    tenancy,
)
from .ai_budget import budget_reached
from .document_content import (
    CriticReview,
    CvContent,
    LetterContent,
    cv_lines,
    letter_length_problem,
    letter_lines,
    roles_changed,
    style_problems,
    unsupported_facts,
)
from .util import now_iso, now_local

log = logging.getLogger("jobcraft")

ACCEPT_SCORE = 85
OWN_LAYOUT = "eigen"
STYLE_KEY = "doc_style"
TONES = {
    "warmer": "Maak de toon warmer en persoonlijker, zonder overdreven te worden.",
    "zakelijker": "Maak de toon zakelijker en rustiger, en houd hem menselijk.",
    "korter": "Maak het duidelijk korter en houd alleen het sterkste over.",
    "gedurfder": "Maak het gedurfder en opvallender: een sterkere opening en een eigen stem.",
}


CHECKED = "nagekeken"  # a rewrite that takes the points under "Controleer dit" away
_QUOTED = re.compile("[\"'\u2018\u201c]([^\"'\u2019\u201d]{2,80})[\"'\u2019\u201d]")


def check_points(review: dict) -> list[str]:
    """What to check before sending, each point once: two notes about the same quote are one."""
    seen: set[str] = set()
    points = []
    for item in [*(review.get("warnings") or []), *(review.get("unsupported") or [])]:
        quoted = _QUOTED.findall(item)
        key = (quoted[0] if quoted else item).casefold().strip()
        if key not in seen:
            seen.add(key)
            points.append(item)
    return points


def _instruction(tone: str, meta: dict) -> str:
    if tone == CHECKED:
        points = "\n".join(f"- {item}" for item in check_points(meta.get("review") or {}))
        return (
            "Verander alleen wat nodig is zodat deze punten niet meer gelden: haal wat niet in de "
            "gegevens staat weg, of zeg alleen wat er wel staat. Houd de rest en de stem gelijk.\n" + points
        )
    return TONES[tone]


def _lines(kind: str, content: dict) -> list[str]:
    return cv_lines(content) if kind == "resume" else letter_lines(content)


def _hard_problems(kind: str, content: dict, source: str) -> list[str]:
    lines = _lines(kind, content)
    problems = unsupported_facts(lines, source) + style_problems(lines)
    if kind == "resume":
        problems += roles_changed(content, source)
    else:
        problem = letter_length_problem(content)
        if problem:
            problems.append(problem)
    return problems


def _write_prompt(kind: str, job: dict, plan: dict, feedback: str = "", draft: dict | None = None) -> str:
    rules = standards.standards_block(("cv-brief-standaarden",))
    shape = (
        "Geef JSON met: headline, profile, highlights (3 of 4), experience (elk {role, organisation, "
        "period, bullets}), education (elk {title, organisation, period}), skills, languages."
        if kind == "resume"
        else "Geef JSON met: salutation, paragraphs (3 of 4 alinea's)."
    )
    revision = (
        f"\n\n## Je vorige versie\n{json.dumps(draft, ensure_ascii=False)}\n\n"
        f"## Commentaar van de recruiter, verwerk alles\n{feedback}\n"
        "Behoud wat goed werkt en vooral de eigen stem. Haal niet-onderbouwde dingen weg in plaats "
        "van ze te herformuleren, en maak de tekst daarbij niet vlakker of korter dan nodig."
        if draft
        else ""
    )
    return (
        f"## Vacature\n{job.get('title')} bij {job.get('company') or 'onbekende organisatie'}\n"
        f"{str(job.get('description') or '')[:7000]}\n\n"
        f"## Bronmateriaal over de persoon (de enige toegestane feiten)\n"
        f"{application_plan.person_source(plan)}\n\n"
        f"## Sollicitatieplan\n{application_plan.writing_brief(plan)}\n\n{rules}{revision}\n\n{shape}"
    )


def _critic_prompt(kind: str, job: dict, plan: dict, content: dict, hard: list[str]) -> str:
    known = f"\n\nAl automatisch gevonden, neem dit mee: {'; '.join(hard)}" if hard else ""
    return (
        f"Dit is een {'cv' if kind == 'resume' else 'motivatiebrief'}.\n\n"
        f"## Vacature\n{job.get('title')} bij {job.get('company') or 'onbekende organisatie'}\n"
        f"{str(job.get('description') or '')[:6000]}\n\n"
        f"## Toegestane feiten over de persoon\n{application_plan.person_source(plan)}\n\n"
        f"## Toegestane feiten over de werkgever\n"
        f"{json.dumps((plan.get('research') or {}).get('employer_facts') or [], ensure_ascii=False)}\n"
        f"Contactpersoon: {(plan.get('research') or {}).get('contact_person') or 'onbekend'}\n\n"
        f"## Te beoordelen\n{json.dumps(content, ensure_ascii=False)}{known}\n\n"
        "Geef JSON met: score, first_impression (wat een recruiter in zes seconden ziet), strongest, "
        "unsupported, fixes."
    )


def write_reviewed(mgr, job: dict, kind: str, plan: dict, emit=lambda stage: None) -> tuple[dict, dict]:
    """A draft, a critical read, and one revision when it needs it. Returns content and review."""
    agent, model = ("cv_writer", CvContent) if kind == "resume" else ("letter_writer", LetterContent)
    ref = f"job:{job['id']}"
    source = application_plan.person_source(plan) + "\n" + application_plan.employer_source(job, plan)
    emit("cv" if kind == "resume" else "letter")
    content = application_plan.call_json(mgr, _write_prompt(kind, job, plan), agent=agent, model=model, ref=ref)
    hard = _hard_problems(kind, content, source)
    if budget_reached():
        return content, {"score": None, "warnings": hard}
    emit("review")
    review = application_plan.call_json(
        mgr, _critic_prompt(kind, job, plan, content, hard), agent="doc_critic", model=CriticReview, ref=ref
    )
    if (hard or review["unsupported"] or review["score"] < ACCEPT_SCORE) and not budget_reached():
        emit("revise")
        notes = "\n".join(f"- {item}" for item in [*hard, *review["unsupported"], *review["fixes"]])
        better = application_plan.call_json(
            mgr, _write_prompt(kind, job, plan, notes, content), agent=agent, model=model, ref=ref
        )
        better_hard = _hard_problems(kind, better, source)
        if len(better_hard) <= len(hard):
            content, hard = better, better_hard
            if not budget_reached():
                emit("review")
                review = application_plan.call_json(
                    mgr,
                    _critic_prompt(kind, job, plan, content, hard),
                    agent="doc_critic",
                    model=CriticReview,
                    ref=ref,
                )
    return content, {**review, "warnings": hard}


# ---------------------------------------------------------------- layout and versions


def preferred_style() -> dict:
    try:
        saved = json.loads(store.meta_get(STYLE_KEY) or "{}")
    except ValueError:
        saved = {}
    return {
        "design": saved.get("design") or document_design.DEFAULT_DESIGN,
        "colour": saved.get("colour") or document_design.DEFAULT_COLOUR,
    }


def own_layout_available() -> bool:
    from . import cv_uploads

    path = config.user_path(docs.CV_TEMPLATE, "templates")
    return path.exists() and not cv_uploads.is_generated()


def _pages(pdf: Path | None) -> int:
    if not pdf:
        return 0
    try:
        return len(PdfReader(str(pdf)).pages)
    except Exception:
        return 0


def _convert(docx_path: Path) -> tuple[Path | None, str | None]:
    try:
        return docs.convert_pdf(docx_path), None
    except (subprocess.SubprocessError, OSError) as exc:
        return None, type(exc).__name__


def _render_own(job: dict, content: dict, docx_path: Path) -> None:
    raw = config.user_path(docs.CV_TEMPLATE, "templates").read_bytes()
    count = docs.template_count(Document(io.BytesIO(raw)))
    points = [*content["highlights"], *content["skills"]][:count]
    points += [""] * (count - len(points))
    docs.render_cv(job, {"profile": content["profile"], "strong_points": points}, docx_path, template_bytes=raw)


def _render_fitted(kind: str, job: dict, content: dict, docx_path: Path, design: str, colour: str, language: str):
    """Tighten step by step until it fits; a long career may take a second CV page."""
    contact = document_contact.load()
    date_line = ", ".join(
        part for part in (contact.get("place"), now_local().strftime("%d-%m-%Y")) if part
    )
    if kind == "resume" and design == OWN_LAYOUT:
        _render_own(job, content, docx_path)
        pdf, error = _convert(docx_path)
        return pdf, error, _pages(pdf)

    def attempt(density: int):
        the_look = document_design.look(design, colour, density)
        if kind == "resume":
            document_design.render_cv(content, contact, docx_path, the_look, language=language)
        else:
            document_letter.render_letter(content, contact, job, date_line, docx_path, the_look, language=language)
        return _convert(docx_path)

    pdf, error = None, None
    for density in range(len(document_design.DENSITIES)):
        pdf, error = attempt(density)
        if _pages(pdf) <= 1:
            return pdf, error, _pages(pdf)
    if kind == "resume":
        pdf, error = attempt(1)  # Two comfortable pages beat one unreadable one.
    return pdf, error, _pages(pdf)


def save_version(
    job: dict,
    kind: str,
    content: dict,
    *,
    design: str | None = None,
    colour: str | None = None,
    review: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """Always a new file: a generated document is never overwritten."""
    style = preferred_style()
    design = design or style["design"]
    colour = colour or style["colour"]
    plan = application_plan.load(job["id"]) or {}
    language = (plan.get("research") or {}).get("language", "nl")
    directory = docs._job_dir(job)
    docs._job_json(job, directory)
    version = docs._next_document_version(directory, kind, store.list_documents(job["id"]))
    stem = f"{kind}-v{version}"
    docx_path = directory / f"{stem}.docx"
    pdf, pdf_error, pages = _render_fitted(kind, job, content, docx_path, design, colour, language)
    meta = {
        "format": 2,
        "kind": kind,
        "version": version,
        "job_id": job["id"],
        "created_at": now_iso(),
        "content": content,
        "design": design,
        "colour": colour,
        "language": language,
        "pages": pages,
        "review": review or {},
        "angle": plan.get("angle"),
        "pdf_error": pdf_error,
        "contact_key": document_contact.key(document_contact.load()),
        **(extra or {}),
    }
    (directory / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    document_id = store.add_document(
        job["id"],
        kind,
        version,
        str(docx_path.relative_to(config.DATA_DIR)),
        str(pdf.relative_to(config.DATA_DIR)) if pdf else None,
        json.dumps(meta, ensure_ascii=False),
    )
    return {"id": document_id, "version": version, "meta": meta}


def create(mgr, job: dict, kind: str, emit=lambda stage: None) -> dict:
    """Write one document from the plan, making the plan first when there is none."""
    plan = application_plan.load(job["id"]) or application_plan.make(mgr, job, emit)
    with docs._generation_lock(tenancy.user_id(), job["id"], kind):
        content, review = write_reviewed(mgr, job, kind, plan, emit)
        emit("layout")
        return save_version(job, kind, content, review=review)


def create_all(mgr, job: dict, kinds: tuple[str, ...], emit=lambda stage: None) -> int | None:
    """CV and letter at the same time, from one plan, so nobody waits for them in turn."""
    if not application_plan.load(job["id"]):
        application_plan.make(mgr, job, emit)
    if len(kinds) == 1:
        return create(mgr, job, kinds[0], emit)["version"]
    with ThreadPoolExecutor(max_workers=len(kinds)) as pool:
        futures = [pool.submit(tenancy.capture(create), mgr, job, kind, emit) for kind in kinds]
        return [future.result() for future in futures][-1]["version"]


def retone(mgr, job: dict, kind: str, content: dict, meta: dict, tone: str) -> dict:
    """Paid, on request. The same fact checks apply as to a first draft."""
    plan = application_plan.load(job["id"]) or {}
    agent, model = ("cv_writer", CvContent) if kind == "resume" else ("letter_writer", LetterContent)
    prompt = _write_prompt(kind, job, plan, _instruction(tone, meta), content)
    with docs._generation_lock(tenancy.user_id(), job["id"], kind):
        better = application_plan.call_json(mgr, prompt, agent=agent, model=model, ref=f"job:{job['id']}")
        source = application_plan.person_source(plan) + "\n" + application_plan.employer_source(job, plan)
        return save_version(
            job,
            kind,
            better,
            design=meta.get("design"),
            colour=meta.get("colour"),
            review={"score": None, "warnings": _hard_problems(kind, better, source)},
            extra={"tone": tone},
        )
