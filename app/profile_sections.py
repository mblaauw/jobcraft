"""A profile is markdown in named sections: taking it apart and back, and the part of it
that may be shown to an AI or an employer."""

from __future__ import annotations

import re
import unicodedata


def split_sections(markdown: str) -> list[dict]:
    """Split markdown on '# ' and '## ' headings; the preamble becomes heading ''.

    Level-one headings count too, except the title on the first line: a pasted
    document often uses them for whole parts (notes, sources), and those must not run
    into the section above. The level is kept so that writing a section back leaves
    the person's own markup alone.
    """
    sections: list[dict] = []
    heading, level = "", 2
    lines: list[str] = []
    seen_text = False
    for line in (markdown or "").splitlines():
        marker = "## " if line.startswith("## ") else "# " if line.startswith("# ") and seen_text else ""
        seen_text = seen_text or bool(line.strip())  # a "# " on the first line is the title
        if marker:
            if heading or any(part.strip() for part in lines):
                sections.append({"heading": heading, "text": "\n".join(lines).strip(), "level": level})
            heading, level = line[len(marker) :].strip(), len(marker) - 1
            lines = []
        else:
            lines.append(line)
    if heading or any(part.strip() for part in lines):
        sections.append({"heading": heading, "text": "\n".join(lines).strip(), "level": level})
    return sections


def join_sections(sections: list[dict]) -> str:
    """The reverse of split_sections, keeping each heading's level."""
    return "\n\n".join(
        (f"{'#' * item.get('level', 2)} {item['heading']}\n" if item["heading"] else "") + item["text"]
        for item in sections
    )


def is_hidden_heading(heading: str) -> bool:
    """Privé and Open vragen, however they were typed: "Prive", "privé:", "Open Vragen"."""
    plain = unicodedata.normalize("NFKD", heading or "").encode("ascii", "ignore").decode().casefold()
    plain = " ".join(re.sub(r"[^a-z]+", " ", plain).split())
    return plain.startswith(("prive", "open vragen"))


_SUBHEADING = re.compile(r"^(#{3,6})\s+(.*)$")


def _without_hidden_parts(body: str) -> str:
    """A "### Privé" inside a public section is as private as a section of that name."""
    kept: list[str] = []
    hidden_level = 0
    for line in body.splitlines():
        found = _SUBHEADING.match(line)
        if found and hidden_level and len(found.group(1)) <= hidden_level:
            hidden_level = 0
        if found and not hidden_level and is_hidden_heading(found.group(2)):
            hidden_level = len(found.group(1))
        if not hidden_level:
            kept.append(line)
    return "\n".join(kept).strip()


def _shortened(chunk: str, room: int) -> str:
    """As much of a section as fits, ending on a whole paragraph or line."""
    if room < 200:
        return ""
    cut = chunk[:room]
    for mark in ("\n\n", "\n", ". "):
        at = cut.rfind(mark)
        if at >= room // 2:
            return cut[: at + (1 if mark == ". " else 0)].rstrip()
    return cut.rstrip()


def public_excerpt(markdown: str, max_chars: int = 6000, skip: tuple[str, ...] = (), fill: bool = False) -> str:
    """Sections safe for letters/CVs/matching: no private or open-questions blocks.

    Over the limit the text stops at the last section that fits whole. With `fill`, every
    section keeps a fair share instead, so one long section cannot push out all the others.
    """
    chunks: list[str] = []
    for section in split_sections(markdown):
        heading = section["heading"]
        if is_hidden_heading(heading) or heading in skip:
            continue
        body = _without_hidden_parts(section["text"])
        if not body and not heading:
            continue
        chunks.append(f"## {heading}\n{body}".rstrip() if heading else body)
    truncated = False
    if fill and sum(len(chunk) for chunk in chunks) > max_chars:
        truncated = True
        room, left = max(0, max_chars - 2 * len(chunks)), len(chunks)  # the blank lines between them count
        share: dict[int, int] = {}
        for index in sorted(range(len(chunks)), key=lambda item: len(chunks[item])):
            share[index] = min(len(chunks[index]), room // left)
            room, left = room - share[index], left - 1
        chunks = [
            chunk if share[index] >= len(chunk) else _shortened(chunk, share[index])
            for index, chunk in enumerate(chunks)
        ]
        parts = [chunk for chunk in chunks if chunk]
    else:
        parts, total = [], 0
        for chunk in chunks:
            if total + len(chunk) > max_chars:
                truncated = True
                break
            parts.append(chunk)
            total += len(chunk)
    text = "\n\n".join(parts).strip()
    if truncated:
        text += "\n\n[profiel-excerpt afgekapt op veilige sectiegrens]"
    return text
