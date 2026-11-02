"""Hand edits become a new immutable version; no AI call, no overwritten file."""

from __future__ import annotations

import difflib
import io
import json

from docx import Document

from . import config, docs, llm_schemas, runtime, store, tenancy
from .docs import template_count
from .util import now_iso, parse_json_object
from .workflows import _budget_reached

REWRITE_STYLES = {
    "korter": "Maak de tekst duidelijk korter en behoud de sterkste punten.",
    "formeler": "Maak de toon formeler en zakelijker.",
}


def edit_problem(exc: Exception) -> str:
    """The checks speak English for the model; a person gets the rule in plain Dutch."""
    message = str(exc)
    if "strong point" in message:
        return "Houd evenveel sterke punten aan als in het voorbeeld, elk hoogstens 70 tekens."
    if "profile must" in message:
        return "Het profielstuk moet tussen 120 en 800 tekens lang zijn."
    if "rendered lines" in message:
        return "De brief past zo niet op één pagina. Maak hem wat korter."
    if "paragraph" in message:
        return "Schrijf minstens één alinea."
    return "Dit kon niet worden opgeslagen. Controleer de tekst en probeer het opnieuw."


def save_edited_version(job, kind: str, content: dict) -> int:
    docs.ensure_templates()
    with docs._generation_lock(tenancy.user_id(), job["id"], kind):
        with docs.template_lock():
            template_path = config.user_path(docs.CV_TEMPLATE, "templates")
            template_bytes = template_path.read_bytes() if template_path.exists() else None
        if kind == "resume":
            strong_count = (
                template_count(Document(io.BytesIO(template_bytes))) if template_bytes else 5
            )
            clean = docs.parse_cv_tailoring(json.dumps(content), strong_count)
        else:
            clean = {"paragraphs": docs.parse_letter(json.dumps(content))}
        directory = docs._job_dir(job)
        version = docs._next_document_version(directory, kind, store.list_documents(job["id"]))
        stem = f"{kind}-v{version}"
        docx_path = directory / f"{stem}.docx"
        if kind == "resume":
            docs.render_cv(job, clean, docx_path, template_bytes=template_bytes)
        else:
            docs.render_letter(job, clean["paragraphs"], docx_path)
        pdf_path, pdf_error = None, None
        try:
            pdf_path = docs.convert_pdf(docx_path)
        except (docs.subprocess.SubprocessError, OSError) as exc:
            pdf_error = type(exc).__name__
        meta = {
            "kind": kind,
            "version": version,
            "job_id": job["id"],
            "created_at": now_iso(),
            "edited": True,
            "content": clean,
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
    return version


def rewrite(job, kind: str, content: dict, style: str) -> int | None:
    """Paid, on request only. Sees just the document itself, so no new private facts enter.

    Returns None when the AI is unavailable or the budget is spent.
    """
    manager = runtime.manager
    if manager is None or manager.client is None or _budget_reached():
        return None
    prompt = (
        f"Herschrijf dit document voor de vacature {job['title']}. {REWRITE_STYLES[style]} "
        "Gebruik alleen feiten die er al in staan en verzin niets. "
        "Antwoord met alleen JSON in precies dezelfde vorm.\n\n"
        + json.dumps(content, ensure_ascii=False)
    )
    if kind == "resume":
        text, usage = manager.generate(
            prompt,
            agent="cv_writer",
            schema=llm_schemas.cv_tailoring_model(
                len(content.get("strong_points") or [])
            ).model_json_schema(),
        )
    else:
        text, usage = manager.generate(prompt, agent="letter_writer")
    docs._record_generation_usage(kind, usage)
    return save_edited_version(job, kind, parse_json_object(text))


def _lines(content: dict) -> list[str]:
    return [content["profile"], *content["strong_points"]] if "profile" in content else list(
        content.get("paragraphs") or []
    )


def changes(old: dict, new: dict) -> list[dict]:
    """Whole lines added, removed or kept between two versions."""
    marks = {"+ ": "add", "- ": "del", "  ": "same"}
    return [
        {"kind": marks[line[:2]], "text": line[2:]}
        for line in difflib.ndiff(_lines(old), _lines(new))
        if line[:2] in marks
    ]
