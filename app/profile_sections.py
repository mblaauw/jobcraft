"""A profile is markdown in named sections; these two functions take it apart and back."""

from __future__ import annotations


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
