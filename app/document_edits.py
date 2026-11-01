"""Hand edits become a new immutable version; no AI call, no overwritten file."""

from __future__ import annotations

import io
import json

from docx import Document

from . import config, docs, store, tenancy
from .docs import template_count
from .util import now_iso


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
