"""Profile text, readable: the small markdown the profile uses, rendered safely.

Profiles are written by the AI and by people, so they mix paragraphs, bullet and
numbered lists, **bold** labels and long runs joined with " · ". Everything is escaped
first; only the few marks below become HTML.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"(?<![*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?![*\w])")
SUBHEADING = re.compile(r"^(#{1,6})\s+(.*)$")
RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
TABLE_DIVIDER = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
NUMBERED = re.compile(r"^(\d+)[.)]\s+(.*)$")
RUN = " · "
# "(vraag 8, 9 — love)": where an answer came from in the interview; noise for the reader.
SOURCE_NOTE = re.compile(r"\s*\((?:vraag|vragen)\s+\d[^()]*\)", re.IGNORECASE)
TEASER_CHARS = 150


def _inline(text: str) -> str:
    """Escape, then bold. A long run joined with dots becomes a short list of its own."""
    text = SOURCE_NOTE.sub("", text)
    html = ITALIC.sub(r"<em>\1</em>", BOLD.sub(r"<strong>\1</strong>", str(escape(text))))
    parts = html.split(RUN)
    if len(parts) >= 3:
        head, _, first = parts[0].rpartition(": ")
        items = [first, *parts[1:]] if head else parts
        lead = f"{head}:" if head else ""
        return lead + "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
    return html


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table(rows: list[str]) -> str:
    """A pipe table; the first row is the header when a divider follows it."""
    header = len(rows) > 1 and TABLE_DIVIDER.match(rows[1].strip())
    body = [row for row in rows if not TABLE_DIVIDER.match(row.strip())]
    html = ['<div class="profile-table"><table>']
    for index, row in enumerate(body):
        tag = "th" if header and index == 0 else "td"
        html.append("<tr>" + "".join(f"<{tag}>{_inline(cell)}</{tag}>" for cell in _cells(row)) + "</tr>")
    html.append("</table></div>")
    return "".join(html)


def render(text: str | None) -> Markup:
    """Paragraphs, lists, subheadings and tables; wrapped lines join into one paragraph."""
    out: list[str] = []
    paragraph: list[str] = []
    table: list[str] = []
    open_list = ""

    def close_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal open_list
        if open_list:
            out.append(f"</{open_list}>")
            open_list = ""

    def close_table() -> None:
        if table:
            out.append(_table(table))
            table.clear()

    for line in (text or "").splitlines():
        stripped = line.strip()
        bullet = stripped[:2] in ("- ", "* ")
        numbered = NUMBERED.match(stripped)
        subheading = SUBHEADING.match(stripped)
        if stripped.startswith("|"):
            close_paragraph()
            close_list()
            table.append(stripped)
            continue
        close_table()
        if subheading or RULE.match(stripped):
            close_paragraph()
            close_list()
            if subheading:  # "### JUN 2024 – HEDEN — Adviseur | Werkgever"
                out.append(f'<h4 class="profile-sub">{_inline(subheading.group(2))}</h4>')
        elif bullet or numbered:
            close_paragraph()
            kind = "ul" if bullet else "ol"
            if open_list != kind:
                close_list()
                out.append(f"<{kind}>")
                open_list = kind
            out.append(f"<li>{_inline(stripped[2:] if bullet else numbered.group(2))}</li>")
        elif not stripped:
            close_paragraph()
            close_list()
        elif line[:1] in (" ", "\t") and open_list and out[-1].endswith("</li>"):
            out[-1] = out[-1][: -len("</li>")] + " " + _inline(stripped) + "</li>"  # a wrapped list item
        else:
            close_list()
            paragraph.append(stripped)
    close_paragraph()
    close_list()
    close_table()
    return Markup("".join(out))


def teaser(text: str | None) -> str:
    """The first sentence or so, as plain text, to show on a closed card."""
    pieces: list[str] = []
    joinable = False  # a wrapped line belongs to the paragraph or point above it
    for raw in (text or "").splitlines():
        line = SOURCE_NOTE.sub("", raw).strip()
        if not line or RULE.match(line) or line.startswith("|"):
            joinable = False
            continue
        marker = re.match(r"^(?:[-*]|\d+[.)]|#{1,6})\s+", line)
        if pieces and joinable and not marker:
            pieces[-1] += " " + line
        else:
            pieces.append(line[marker.end() :] if marker else line)
        joinable = not line.startswith("#")
    if len(pieces) > 1 and BOLD.fullmatch(pieces[0]):  # a label line says little on its own
        pieces = pieces[1:]
    plain = ""
    for piece in (ITALIC.sub(r"\1", BOLD.sub(r"\1", piece)) for piece in pieces):
        plain += piece if not plain else ((" " if plain[-1] in ".;:!?" else "; ") + piece)
    plain = " ".join(plain.split()).replace(RUN, ", ")
    if len(plain) <= TEASER_CHARS:
        return plain
    cut = plain[:TEASER_CHARS]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: end + 1] if end > 60 else cut.rsplit(" ", 1)[0]) + " …"
