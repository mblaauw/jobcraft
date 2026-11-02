"""The look of a CV and letter: a real Word file, also the source of the PDF.

Fonts are Calibri and Cambria because Word has them everywhere and LibreOffice swaps in
metric-identical twins, so the Word file and the PDF break lines in the same places. All
text stays real text in reading order, which is what an applicant tracking system needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

DESIGNS = {
    "kantlijn": "Kantlijn: jaren en koppen in een smalle kolom, rustig en makkelijk te scannen",
    "helder": "Helder: één kolom, het veiligst voor automatische cv-systemen",
    "blok": "Blok: je naam in een vlak van kleur, voor wie mag opvallen",
}
COLOURS = {
    "dennen": ("Dennengroen", "1F4D3A", "EDF3EE"),
    "petrol": ("Petrol", "0F4C5C", "E9F2F4"),
    "aubergine": ("Aubergine", "54294F", "F4EDF3"),
    "grafiet": ("Grafiet", "2F343B", "F0F1F2"),
}
DEFAULT_DESIGN = "kantlijn"
DEFAULT_COLOUR = "dennen"
HEADING_FONT = "Cambria"
BODY_FONT = "Calibri"
INK = RGBColor(0x22, 0x26, 0x2B)
SOFT = RGBColor(0x5A, 0x62, 0x6B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PAGE_WIDTH_CM = 21.0
MARGIN_CM = 1.9
LABEL_COLUMN_CM = 3.3
# Roomy first, so a short CV fills its page; tighter steps until a long one fits.
DENSITIES = (
    {"body": 11, "gap": 1.25, "section": 15, "name": 30},
    {"body": 10.5, "gap": 1.0, "section": 11, "name": 28},
    {"body": 10, "gap": 0.8, "section": 8, "name": 26},
    {"body": 9.5, "gap": 0.6, "section": 6, "name": 24},
)


@dataclass(frozen=True)
class Look:
    design: str
    accent: RGBColor
    accent_hex: str
    tint_hex: str
    density: dict


def look(design: str = DEFAULT_DESIGN, colour: str = DEFAULT_COLOUR, density: int = 0) -> Look:
    _label, accent, tint = COLOURS.get(colour, COLOURS[DEFAULT_COLOUR])
    return Look(
        design if design in DESIGNS else DEFAULT_DESIGN,
        RGBColor.from_string(accent),
        accent,
        tint,
        DENSITIES[max(0, min(density, len(DENSITIES) - 1))],
    )


# ---------------------------------------------------------------- word primitives


def _new_document(the_look: Look) -> Document:
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(PAGE_WIDTH_CM), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(1.7)
    section.left_margin = section.right_margin = Cm(MARGIN_CM)
    style = document.styles["Normal"]
    style.font.name = BODY_FONT
    style.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    style.font.size = Pt(the_look.density["body"])
    style.font.color.rgb = INK
    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.line_spacing = 1.12
    return document


def _run(paragraph, text: str, *, size=None, bold=None, colour=None, font=None, italic=None):
    run = paragraph.add_run(text)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if colour is not None:
        run.font.color.rgb = colour
    if font:
        run.font.name = font
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), font)
    return run


def _spacing(paragraph, before: float = 0, after: float = 0, line: float | None = None):
    paragraph.paragraph_format.space_before = Pt(before)
    paragraph.paragraph_format.space_after = Pt(after)
    if line:
        paragraph.paragraph_format.line_spacing = line
    return paragraph


def _rule(paragraph, hex_colour: str, size: int = 8, space: int = 4) -> None:
    properties = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("val", "single"), ("sz", str(size)), ("space", str(space)), ("color", hex_colour)):
        bottom.set(qn(f"w:{key}"), value)
    borders.append(bottom)
    properties.append(borders)


def _shade(cell, hex_colour: str) -> None:
    shading = OxmlElement("w:shd")
    for key, value in (("val", "clear"), ("color", "auto"), ("fill", hex_colour)):
        shading.set(qn(f"w:{key}"), value)
    cell._tc.get_or_add_tcPr().append(shading)


def _cell_margins(cell, top=0, bottom=0, left=0, right=0) -> None:
    margins = OxmlElement("w:tcMar")
    for side, value in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), str(int(value * 567)))  # cm to twentieths of a point
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    cell._tc.get_or_add_tcPr().append(margins)


def _table(document, widths: list[float]):
    table = document.add_table(rows=0, cols=len(widths))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    # No table padding or indent, so its text lines up with the paragraphs around it.
    properties = table._tbl.tblPr
    indent = OxmlElement("w:tblInd")
    indent.set(qn("w:w"), "0")
    indent.set(qn("w:type"), "dxa")
    properties.append(indent)
    margins = OxmlElement("w:tblCellMar")
    for side in ("left", "right"):
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), "0")
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    properties.append(margins)
    for column, width in zip(table.columns, widths, strict=True):
        column.width = Cm(width)
    return table


def _row(table, widths: list[float], *, keep_together: bool = True):
    row = table.add_row()
    for cell, width in zip(row.cells, widths, strict=True):
        cell.width = Cm(width)
        _cell_margins(cell)
    if keep_together:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    return row


def _first(cell):
    return cell.paragraphs[0]


def _hanging(paragraph, the_look: Look, *, after: float = 1.5):
    paragraph.paragraph_format.left_indent = Cm(0.38)
    paragraph.paragraph_format.first_line_indent = Cm(-0.38)
    paragraph.paragraph_format.tab_stops.add_tab_stop(Cm(0.38), WD_TAB_ALIGNMENT.LEFT)
    _spacing(paragraph, after=after * the_look.density["gap"])
    # A typed bullet: Word's list symbol font comes out as garbage in many CV systems.
    _run(paragraph, "•\t", colour=the_look.accent)
    return paragraph


def _bullet(container, text: str, the_look: Look, *, after: float = 1.5):
    paragraph = _hanging(container.add_paragraph(), the_look, after=after)
    if text:
        _run(paragraph, text)
    return paragraph


# ---------------------------------------------------------------- shared pieces


def contact_line(contact: dict) -> str:
    parts = [contact.get(key, "").strip() for key in ("place", "phone", "email", "linkedin")]
    return "   ·   ".join(part for part in parts if part)


def _masthead(document, contact: dict, tagline: str, the_look: Look) -> None:
    name = contact.get("name") or ""
    size = the_look.density["name"]
    line = contact_line(contact)
    if the_look.design == "blok":
        width = PAGE_WIDTH_CM - 2 * MARGIN_CM
        table = _table(document, [width])
        cell = _row(table, [width]).cells[0]
        _shade(cell, the_look.accent_hex)
        cell._tc.get_or_add_tcPr().remove(cell._tc.get_or_add_tcPr().find(qn("w:tcMar")))
        _cell_margins(cell, top=0.45, bottom=0.45, left=0.55, right=0.55)
        _run(_first(cell), name, size=size, font=HEADING_FONT, colour=WHITE, bold=True)
        if tagline:
            _spacing(cell.add_paragraph(), before=2).add_run(tagline).font.color.rgb = WHITE
        if line:
            detail = _spacing(cell.add_paragraph(), before=4)
            _run(detail, line, size=9, colour=WHITE)
        _spacing(document.add_paragraph(), after=2)
        return
    heading = _spacing(document.add_paragraph(), after=1, line=1.0)
    _run(heading, name, size=size, font=HEADING_FONT, colour=the_look.accent, bold=True)
    if tagline:
        _run(_spacing(document.add_paragraph(), after=2), tagline, size=the_look.density["body"] + 1.5)
    detail = _spacing(document.add_paragraph(), after=3)
    _run(detail, line, size=9, colour=SOFT)
    _rule(detail, the_look.accent_hex, size=10, space=6)


# ---------------------------------------------------------------- cv


def _section_title(paragraph, text: str, the_look: Look) -> None:
    _run(paragraph, text, size=the_look.density["body"] + 1, bold=True, font=HEADING_FONT, colour=the_look.accent)


def _role_heading(paragraph, role: dict, the_look: Look) -> None:
    _run(paragraph, role["role"], bold=True)
    if role.get("organisation"):
        _run(paragraph, f", {role['organisation']}")


def _highlight_block(document, heading: str, items: list[str], the_look: Look, width: float) -> None:
    table = _table(document, [width])
    cell = _row(table, [width]).cells[0]
    _shade(cell, the_look.tint_hex)
    cell._tc.get_or_add_tcPr().remove(cell._tc.get_or_add_tcPr().find(qn("w:tcMar")))
    _cell_margins(cell, top=0.22, bottom=0.2, left=0.3, right=0.3)
    _section_title(_spacing(_first(cell), after=3), heading, the_look)
    for item in items:
        _bullet(cell, item, the_look, after=2)


def _render_kantlijn(document, content: dict, labels: dict, the_look: Look) -> None:
    total = PAGE_WIDTH_CM - 2 * MARGIN_CM
    widths = [LABEL_COLUMN_CM, total - LABEL_COLUMN_CM]
    table = _table(document, widths)
    gap = the_look.density["section"]

    def add(label: str, *, before: float = gap, keep: bool = True):
        left, right = _row(table, widths, keep_together=keep).cells
        _cell_margins(right, left=0.15)
        paragraph = _spacing(_first(left), before=before)
        if label:
            _section_title(paragraph, label, the_look)
        _spacing(_first(right), before=before)
        return left, right

    _left, right = add(labels["profile"], before=8)
    _spacing(_first(right), before=8, after=gap * 0.8)
    _run(_first(right), content["profile"])
    if content.get("highlights"):
        left, right = add(labels["highlights"], before=0)
        for cell in (left, right):  # The band is what the eye lands on in the top third.
            _shade(cell, the_look.tint_hex)
            cell._tc.get_or_add_tcPr().remove(cell._tc.get_or_add_tcPr().find(qn("w:tcMar")))
        _cell_margins(left, top=0.16, bottom=0.14, left=0.12)
        _cell_margins(right, top=0.16, bottom=0.08, left=0.15, right=0.2)
        # An empty spacer row keeps the band clear of the profile text above it.
        for index, item in enumerate(content["highlights"]):
            bullet = _hanging(_first(right), the_look) if index == 0 else _bullet(right, "", the_look)
            _run(bullet, item)
    for index, role in enumerate(content.get("experience", [])):
        left, right = add(labels["experience"] if index == 0 else "", before=gap if index == 0 else 6)
        if index == 0:
            _spacing(left.add_paragraph(), before=1)
            period = left.paragraphs[-1]
        else:
            period = _first(left)
        _run(period, role.get("period", ""), size=9, colour=SOFT)
        _role_heading(_spacing(_first(right), before=gap if index == 0 else 6, after=1.5), role, the_look)
        for bullet in role.get("bullets", []):
            _bullet(right, bullet, the_look)
    if content.get("education"):
        _left, right = add(labels["education"])
        for index, item in enumerate(content["education"]):
            paragraph = _first(right) if index == 0 else right.add_paragraph()
            _spacing(paragraph, before=gap if index == 0 else 0, after=1.5)
            _run(paragraph, item["title"], bold=True)
            details = ", ".join(part for part in (item.get("organisation"), item.get("period")) if part)
            if details:
                _run(paragraph, f", {details}")
    if content.get("skills"):
        _left, right = add(labels["skills"])
        _run(_first(right), ", ".join(content["skills"]))
    if content.get("languages"):
        _left, right = add(labels["languages"])
        _run(_first(right), ", ".join(content["languages"]))


def _render_column(document, content: dict, labels: dict, the_look: Look) -> None:
    gap = the_look.density["section"]
    width = PAGE_WIDTH_CM - 2 * MARGIN_CM

    def title(text: str) -> None:
        paragraph = _spacing(document.add_paragraph(), before=gap, after=3)
        paragraph.paragraph_format.keep_with_next = True
        _section_title(paragraph, text, the_look)
        _rule(paragraph, the_look.tint_hex, size=6, space=2)

    _run(_spacing(document.add_paragraph(), before=6, after=gap * 0.7), content["profile"])
    if content.get("highlights"):
        _highlight_block(document, labels["highlights"], content["highlights"], the_look, width)
    if content.get("experience"):
        title(labels["experience"])
        for role in content["experience"]:
            paragraph = _spacing(document.add_paragraph(), before=4, after=1.5)
            paragraph.paragraph_format.keep_with_next = True
            paragraph.paragraph_format.tab_stops.add_tab_stop(Cm(width), WD_TAB_ALIGNMENT.RIGHT)
            _role_heading(paragraph, role, the_look)
            _run(paragraph, f"\t{role.get('period', '')}", size=9, colour=SOFT)
            for bullet in role.get("bullets", []):
                _bullet(document, bullet, the_look)
    if content.get("education"):
        title(labels["education"])
        for item in content["education"]:
            paragraph = _spacing(document.add_paragraph(), after=1.5)
            paragraph.paragraph_format.tab_stops.add_tab_stop(Cm(width), WD_TAB_ALIGNMENT.RIGHT)
            _run(paragraph, item["title"], bold=True)
            if item.get("organisation"):
                _run(paragraph, f", {item['organisation']}")
            _run(paragraph, f"\t{item.get('period', '')}", size=9, colour=SOFT)
    if content.get("skills"):
        title(labels["skills"])
        _run(document.add_paragraph(), ", ".join(content["skills"]))
    if content.get("languages"):
        title(labels["languages"])
        _run(document.add_paragraph(), ", ".join(content["languages"]))


CV_LABELS = {
    "nl": {
        "profile": "Profiel",
        "highlights": "Voor deze rol",
        "experience": "Werkervaring",
        "education": "Opleiding",
        "skills": "Vaardigheden",
        "languages": "Talen",
    },
    "en": {
        "profile": "Profile",
        "highlights": "For this role",
        "experience": "Experience",
        "education": "Education",
        "skills": "Skills",
        "languages": "Languages",
    },
}


def render_cv(content: dict, contact: dict, out_path: Path, the_look: Look, *, language: str = "nl") -> None:
    document = _new_document(the_look)
    _masthead(document, contact, content.get("headline", ""), the_look)
    labels = CV_LABELS.get(language, CV_LABELS["nl"])
    if the_look.design == "kantlijn":
        _render_kantlijn(document, content, labels, the_look)
    else:
        _render_column(document, content, labels, the_look)
    document.save(str(out_path))
