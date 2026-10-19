"""Tailored CV + motivation letter generation, with docx -> pdf conversion."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import unicodedata
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.text.paragraph import Paragraph

from . import config, llm, profile, standards, store, tenancy
from .util import now_iso, now_local, parse_json_object

DOC_TEMPLATES_DIR = config.DATA_DIR / "templates"
CV_TEMPLATE = DOC_TEMPLATES_DIR / "cv-template.docx"
IDENTITY_FILE = DOC_TEMPLATES_DIR / "identity.json"

LETTER_MAX_LINES = 15
LETTER_CHARS_PER_LINE = 95
_GENERATION_LOCKS: dict[tuple[str, int, str], threading.Lock] = {}
_GENERATION_LOCKS_GUARD = threading.Lock()


def _generation_lock(owner: str, job_id: int, kind: str) -> threading.Lock:
    key = (owner, job_id, kind)
    with _GENERATION_LOCKS_GUARD:
        return _GENERATION_LOCKS.setdefault(key, threading.Lock())


# ---------------------------------------------------------------- templates


def _all_paragraphs(document):
    """Yield every paragraph, including table cells (the CV layout is tabular)."""
    for element in document.element.body.iter(qn("w:p")):
        yield Paragraph(element, document)


def _set_text(paragraph, text: str) -> None:
    """Replace paragraph text while keeping the first run's formatting."""
    if not paragraph.runs:
        paragraph.add_run(text)
        return
    paragraph.runs[0].text = text
    for run in paragraph.runs[1:]:
        run.text = ""


def ensure_templates() -> None:
    config.user_path(CV_TEMPLATE, "templates").parent.mkdir(parents=True, exist_ok=True)
    source = config.seed_dir() / config.seed_files()["cv"]
    if not source.exists():
        raise FileNotFoundError(f"base CV not found: {source}")

    if config.user_path(CV_TEMPLATE, "templates").exists():
        document = Document(str(config.user_path(CV_TEMPLATE, "templates")))
        if not _is_a4(document):
            _apply_a4(document)
            document.save(str(config.user_path(CV_TEMPLATE, "templates")))
        config.user_path(IDENTITY_FILE, "templates").write_text(
            json.dumps(_extract_identity(document), ensure_ascii=False, indent=2)
        )
        return

    document = Document(str(source))

    identity = _extract_identity(document)
    paragraphs = list(_all_paragraphs(document))

    profile_replaced = False
    strong_points: list = []
    collecting = False
    for paragraph in paragraphs:
        text = paragraph.text.strip()
        if text.upper() == "PROFIEL":
            collecting = "profile"
            continue
        if text.upper() == "STERKE PUNTEN":
            collecting = "strong"
            continue
        if collecting == "profile" and text:
            _set_text(paragraph, "{{PROFILE}}")
            profile_replaced = True
            collecting = None
        elif collecting == "strong":
            if paragraph.style is not None and paragraph.style.name == "List Bullet":
                strong_points.append(paragraph)
                if len(strong_points) == 5:
                    collecting = None
            elif text:
                collecting = None

    if not profile_replaced or len(strong_points) != 5:
        raise RuntimeError(
            f"CV template structure unexpected (profile={profile_replaced}, "
            f"strong_points={len(strong_points)})"
        )
    for index, paragraph in enumerate(strong_points, start=1):
        _set_text(paragraph, f"{{{{STRONG_{index}}}}}")

    _apply_a4(document)
    document.save(str(config.user_path(CV_TEMPLATE, "templates")))
    config.user_path(IDENTITY_FILE, "templates").write_text(
        json.dumps(identity, ensure_ascii=False, indent=2)
    )


def _extract_identity(document) -> dict:
    identity: dict[str, list[str]] = {"name": [], "tagline": [], "contact": []}
    for paragraph in _all_paragraphs(document):
        text = paragraph.text.strip()
        if text.upper() == "PROFIEL":
            break
        if not text:
            continue
        if not identity["name"]:
            identity["name"].append(text)
        elif not identity["tagline"]:
            identity["tagline"].append(text)
        else:
            identity["contact"].append(text)
    return identity


def _is_a4(document) -> bool:
    section = document.sections[0]
    return abs(section.page_width - Cm(21.0)) < 1000 and abs(section.page_height - Cm(29.7)) < 1000


def _apply_a4(document) -> None:
    for section in document.sections:
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)


def cv_identity() -> dict:
    ensure_templates()
    return json.loads(config.user_path(IDENTITY_FILE, "templates").read_text())


# ---------------------------------------------------------------- parsing


def slugify(value: str, limit: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return (slug or "job")[:limit].strip("-")


def parse_cv_tailoring(text: str) -> dict:
    data = parse_json_object(text)
    profile = str(data.get("profile") or "").strip()
    strong = [str(item).strip() for item in (data.get("strong_points") or []) if str(item).strip()]
    if not (120 <= len(profile) <= 800):
        raise ValueError("CV profile must be 120-800 characters")
    if len(strong) != 5:
        raise ValueError("CV tailoring needs exactly 5 strong points")
    if any(len(item) > 70 for item in strong):
        raise ValueError("strong points must be at most 70 characters")
    return {"profile": profile, "strong_points": strong}


def parse_letter(text: str) -> list[str]:
    data = parse_json_object(text)
    paragraphs = [str(item).strip() for item in (data.get("paragraphs") or []) if str(item).strip()]
    if not paragraphs:
        raise ValueError("letter needs at least one paragraph")
    visual_lines = sum(max(1, math.ceil(len(item) / LETTER_CHARS_PER_LINE)) for item in paragraphs)
    if visual_lines > LETTER_MAX_LINES:
        raise ValueError(
            f"letter must stay within {LETTER_MAX_LINES} rendered lines "
            f"(estimated {visual_lines}); shorten the text"
        )
    return paragraphs


# ---------------------------------------------------------------- prompts


def cv_prompt(job, profile_md: str) -> str:
    standards_text = standards.standards_block(("cv-brief-standaarden", "vacature-analyse"))
    extra = f"\n\n{standards_text}" if standards_text else ""
    return (
        f"Pas het cv van {tenancy.display_name()} licht aan voor deze vacature. Gebruik alleen feiten uit het profiel.\n"
        "Verzin geen ervaring, opleiding of vaardigheden.\n\n"
        "## Profiel\n"
        f"{profile_md[:7000]}\n\n"
        "## Vacature\n"
        f"{job['title']} bij {job['company'] or 'onbekende organisatie'}\n"
        f"{job['description'][:7000]}"
        f"{extra}\n\n"
        'Geef alleen JSON: {"profile": "<profielparagraaf van 3-4 zinnen, max 600 tekens>", '
        '"strong_points": ["<vijf korte punten, elk max 60 tekens>"]}.'
    )


def letter_prompt(job, profile_md: str) -> str:
    standards_text = standards.standards_block(("cv-brief-standaarden", "vacature-analyse"))
    extra = f"\n\n{standards_text}" if standards_text else ""
    return (
        f"Schrijf een korte Nederlandse motivatiebrief voor {tenancy.display_name()}. Maximaal 15 "
        "gerenderde regels in totaal (ongeveer 1.200 tekens), 3-5 alinea's, concreet "
        "en zonder clichés. Gebruik alleen feiten uit het profiel; verzin niets. "
        "Verwijs naar de vacature en waarom deze persoon daar past. Geen adressen, geen "
        "datum, geen ondertekening in de tekst.\n\n"
        "## Profiel\n"
        f"{profile_md[:7000]}\n\n"
        "## Vacature\n"
        f"{job['title']} bij {job['company'] or 'onbekende organisatie'}\n"
        f"{job['description'][:7000]}"
        f"{extra}\n\n"
        'Geef alleen JSON: {"paragraphs": ["<alinea>", "..."]}.'
    )


# ---------------------------------------------------------------- rendering


def _job_dir(job) -> Path:
    company = job["company"] or "onbekend"
    title = job["title"] or "vacature"
    stamp = now_local().strftime("%Y-%m-%d")
    name = f"{stamp}-{slugify(company, 24)}-{slugify(title, 40)}-{job['id']}"
    directory = config.jobs_dir() / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _job_json(job, directory: Path) -> None:
    payload = {key: job[key] for key in job.keys()}
    payload["generated_at"] = now_iso()
    (directory / "job.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _next_document_version(directory: Path, kind: str, documents: list) -> int:
    """Never reuse a version, even when an interrupted run left orphan files."""
    versions = {doc["version"] for doc in documents if doc["kind"] == kind}
    pattern = re.compile(rf"^{re.escape(kind)}-v(\d+)\.(?:docx|pdf|json)$")
    versions.update(
        int(match.group(1))
        for path in directory.iterdir()
        if path.is_file() and (match := pattern.match(path.name))
    )
    version = max(versions, default=0) + 1
    while any(
        (directory / f"{kind}-v{version}{suffix}").exists() for suffix in (".docx", ".pdf", ".json")
    ):
        version += 1
    return version


def _record_generation_usage(kind: str, usage: dict) -> None:
    store.record_usage(
        None,
        "documents",
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        float(usage.get("cost_usd") or 0.0),
        agent=("cv_writer" if kind == "resume" else "letter_writer"),
    )


def render_cv(job, tailoring: dict, out_path: Path) -> None:
    document = Document(str(config.user_path(CV_TEMPLATE, "templates")))
    values = {"{{PROFILE}}": tailoring["profile"]}
    values.update(
        {f"{{{{STRONG_{i}}}}}": text for i, text in enumerate(tailoring["strong_points"], 1)}
    )
    replaced = 0
    for paragraph in _all_paragraphs(document):
        key = paragraph.text.strip()
        if key in values:
            _set_text(paragraph, values[key])
            replaced += 1
    if replaced != 6:
        raise RuntimeError(f"CV template placeholders missing (replaced {replaced}/6)")
    document.save(str(out_path))


def render_letter(job, paragraphs: list[str], out_path: Path) -> None:
    identity = cv_identity()
    name = (identity.get("name") or [tenancy.display_name()])[0]
    contact = [line for line in identity.get("contact", []) if "Rijbewijs" not in line]
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)

    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.15

    heading = document.add_paragraph()
    run = heading.add_run(name)
    run.bold = True
    run.font.size = Pt(20)
    heading.paragraph_format.space_after = Pt(2)

    if contact:
        line = document.add_paragraph("   |   ".join(contact))
        line.runs[0].font.size = Pt(10)

    document.add_paragraph("")
    city = profile.read_preferences().get("home", {}).get("label", "")
    city = "" if city == "Nog niet ingesteld" else city
    date_line = document.add_paragraph(
        f"{city + ', ' if city else ''}{now_local().strftime('%d-%m-%Y')}"
    )
    date_line.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    employer = document.add_paragraph()
    employer.add_run(job["company"] or "").bold = True
    document.add_paragraph("")

    subject = document.add_paragraph()
    subject.add_run(f"Betreft: sollicitatie naar de functie {job['title']}").bold = True
    document.add_paragraph("")

    for text in paragraphs:
        document.add_paragraph(text)

    document.add_paragraph("")
    document.add_paragraph("Met vriendelijke groet,")
    signature = document.add_paragraph()
    signature.add_run(name).bold = True
    document.save(str(out_path))


# ---------------------------------------------------------------- pdf


def _soffice() -> str | None:
    candidates = [
        os.environ.get("SOFFICE_BIN"),
        shutil.which("soffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def convert_pdf(docx_path: Path) -> Path | None:
    """LibreOffice when present, else the app image as a converter (dev only)."""
    soffice = _soffice()
    if soffice:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(docx_path.parent),
                str(docx_path),
            ],
            check=True,
            timeout=180,
            capture_output=True,
        )
        pdf = docx_path.with_suffix(".pdf")
        return pdf if pdf.exists() else None
    if shutil.which("docker"):
        image = os.environ.get("DOC_PDF_IMAGE", "jobcraft:local")
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{docx_path.parent}:/work",
                image,
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                "/work",
                f"/work/{docx_path.name}",
            ],
            check=True,
            timeout=300,
            capture_output=True,
        )
        pdf = docx_path.with_suffix(".pdf")
        return pdf if pdf.exists() else None
    return None


# ---------------------------------------------------------------- generation


def _generate_job_document(mgr: llm.ServerManager, job, kind: str) -> dict:
    if kind not in ("resume", "motivatie"):
        raise ValueError("kind must be resume or motivatie")
    ensure_templates()
    profile_md = profile.read_public_excerpt(12000)
    directory = _job_dir(job)
    _job_json(job, directory)
    version = _next_document_version(directory, kind, store.list_documents(job["id"]))
    stem = f"{kind}-v{version}"
    docx_path = directory / f"{stem}.docx"
    if docx_path.exists():  # defensive: a version was reserved by an unexpected writer
        raise FileExistsError(f"{docx_path.name} already exists")

    if kind == "resume":
        text, usage = mgr.generate(cv_prompt(job, profile_md), agent="cv_writer")
    else:
        text, usage = mgr.generate(letter_prompt(job, profile_md), agent="letter_writer")
    # Account for the paid call before parsing/rendering can fail.
    _record_generation_usage(kind, usage)
    if kind == "resume":
        render_cv(job, parse_cv_tailoring(text), docx_path)
    else:
        render_letter(job, parse_letter(text), docx_path)

    pdf_path = None
    pdf_error = None
    try:
        pdf_path = convert_pdf(docx_path)
    except (subprocess.SubprocessError, OSError) as exc:
        pdf_error = f"{type(exc).__name__}"

    meta = {
        "kind": kind,
        "version": version,
        "job_id": job["id"],
        "created_at": now_iso(),
        "usage": usage,
        "pdf_error": pdf_error,
    }
    (directory / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    store.add_document(
        job["id"],
        kind,
        version,
        str(docx_path.relative_to(config.DATA_DIR)),
        str(pdf_path.relative_to(config.DATA_DIR)) if pdf_path else None,
        json.dumps(meta, ensure_ascii=False),
    )
    return {
        "docx": str(docx_path),
        "pdf": str(pdf_path) if pdf_path else None,
        "version": version,
        "meta": meta,
    }


def generate_for_job(mgr: llm.ServerManager, job, kind: str) -> dict:
    """Serialize one owner/job/kind without blocking other accounts."""
    with _generation_lock(tenancy.user_id(), job["id"], kind):
        return _generate_job_document(mgr, job, kind)
