"""The letter on the same stationery as the CV, so the two read as one application."""

from __future__ import annotations

from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm

from .document_design import (
    HEADING_FONT,
    PAGE_WIDTH_CM,
    SOFT,
    WHITE,
    Look,
    _cell_margins,
    _first,
    _new_document,
    _row,
    _rule,
    _run,
    _shade,
    _spacing,
    _table,
    contact_line,
)

LETTER_WORDS = {
    "nl": {"subject": "Sollicitatie", "regards": "Met vriendelijke groet,"},
    "en": {"subject": "Application", "regards": "Kind regards,"},
}


def render_letter(
    content: dict,
    contact: dict,
    job: dict,
    date_line: str,
    out_path: Path,
    the_look: Look,
    *,
    language: str = "nl",
) -> None:
    document = _new_document(the_look)
    section = document.sections[0]
    section.top_margin, section.bottom_margin = Cm(1.9), Cm(1.9)
    section.left_margin = section.right_margin = Cm(2.4)
    words = LETTER_WORDS.get(language, LETTER_WORDS["nl"])
    body = the_look.density["body"] + 0.5
    if the_look.design == "blok":
        # The band spans the writing width of this page, not the wider CV column.
        table = _table(document, [PAGE_WIDTH_CM - 4.8])
        cell = _row(table, [PAGE_WIDTH_CM - 4.8]).cells[0]
        _shade(cell, the_look.accent_hex)
        cell._tc.get_or_add_tcPr().remove(cell._tc.get_or_add_tcPr().find(qn("w:tcMar")))
        _cell_margins(cell, top=0.4, bottom=0.4, left=0.5, right=0.5)
        _run(_first(cell), contact.get("name") or "", size=the_look.density["name"] - 4, font=HEADING_FONT, colour=WHITE, bold=True)
        _run(_spacing(cell.add_paragraph(), before=3), contact_line(contact), size=9, colour=WHITE)
    else:
        heading = _spacing(document.add_paragraph(), after=2, line=1.0)
        _run(heading, contact.get("name") or "", size=the_look.density["name"] - 4, font=HEADING_FONT, colour=the_look.accent, bold=True)
        detail = _spacing(document.add_paragraph())
        _run(detail, contact_line(contact), size=9, colour=SOFT)
        _rule(detail, the_look.accent_hex, size=10, space=6)

    address = _spacing(document.add_paragraph(), before=20, after=0)
    _run(address, job.get("company") or "", bold=True, size=body)
    dated = _spacing(document.add_paragraph(), before=2, after=14)
    _run(dated, date_line, size=9.5, colour=SOFT)
    subject = _spacing(document.add_paragraph(), after=12)
    _run(subject, f"{words['subject']}: {job.get('title') or ''}", bold=True, font=HEADING_FONT, size=body + 1, colour=the_look.accent)
    _run(_spacing(document.add_paragraph(), after=8), content["salutation"], size=body)
    for text in content["paragraphs"]:
        paragraph = _spacing(document.add_paragraph(), after=8, line=1.22)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _run(paragraph, text, size=body)
    regards = _spacing(document.add_paragraph(), before=8, after=4)
    regards.paragraph_format.keep_with_next = True
    _run(regards, words["regards"], size=body)
    _run(_spacing(document.add_paragraph()), contact.get("name") or "", bold=True, size=body)
    document.save(str(out_path))
