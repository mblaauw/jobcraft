"""Profile text, readable: the small markdown the profile uses, rendered safely.

Profiles are written by the AI and by people, so they mix paragraphs, bullet and
numbered lists, **bold** labels and long runs joined with " · ". Everything is escaped
first; only the few marks below become HTML.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

BOLD = re.compile(r"\*\*(.+?)\*\*")
NUMBERED = re.compile(r"^(\d+)[.)]\s+(.*)$")
RUN = " · "
# "(vraag 8, 9 — love)": where an answer came from in the interview; noise for the reader.
SOURCE_NOTE = re.compile(r"\s*\((?:vraag|vragen)\s+\d[^()]*\)", re.IGNORECASE)
TEASER_CHARS = 150


def _inline(text: str) -> str:
    """Escape, then bold. A long run joined with dots becomes a short list of its own."""
    text = SOURCE_NOTE.sub("", text)
    html = BOLD.sub(r"<strong>\1</strong>", str(escape(text)))
    parts = html.split(RUN)
    if len(parts) >= 3:
        head, _, first = parts[0].rpartition(": ")
        items = [first, *parts[1:]] if head else parts
        lead = f"{head}:" if head else ""
        return lead + "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
    return html


def render(text: str | None) -> Markup:
    """Paragraphs, bullet lists and numbered lists; wrapped lines join into one paragraph."""
    out: list[str] = []
    paragraph: list[str] = []
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

    for line in (text or "").splitlines():
        stripped = line.strip()
        bullet = stripped[:2] in ("- ", "* ")
        numbered = NUMBERED.match(stripped)
        if bullet or numbered:
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
        elif not stripped.startswith("#"):
            close_list()
            paragraph.append(stripped)
    close_paragraph()
    close_list()
    return Markup("".join(out))


def teaser(text: str | None) -> str:
    """The first sentence or so, as plain text, to show on a closed card."""
    lines = [SOURCE_NOTE.sub("", line).strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) > 1 and BOLD.fullmatch(lines[0]):  # a label line says little on its own
        lines = lines[1:]
    pieces = [re.sub(r"^(?:[-*]|\d+[.)])\s+", "", BOLD.sub(r"\1", line)) for line in lines]
    plain = ""
    for piece in pieces:  # list items without a full stop still read as separate points
        plain += piece if not plain else ((" " if plain[-1] in ".;:!?" else "; ") + piece)
    plain = " ".join(plain.split()).replace(RUN, ", ")
    if len(plain) <= TEASER_CHARS:
        return plain
    cut = plain[:TEASER_CHARS]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: end + 1] if end > 60 else cut.rsplit(" ", 1)[0]) + " …"
