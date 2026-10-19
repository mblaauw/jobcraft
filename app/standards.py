"""Project standards loaded from `.opencode/skills`.

The skill files are the single source of truth for document standards and
vacancy decoding. The app injects them into generation prompts so the GUI flow and
the interactive agent follow the same rules. A missing skill is tolerated: the
prompt is then built without the block (for example a container built before the
skills existed).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from . import config, tenancy

SKILLS_DIR = config.BASE_DIR / ".opencode" / "skills"


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip("\n")
    return text


@lru_cache(maxsize=16)
def _read_cached(path_str: str, mtime: float, size: int) -> str:
    try:
        return _strip_frontmatter(Path(path_str).read_text())
    except OSError:
        return ""


def load_skill(name: str) -> str:
    """Return the markdown body of one skill, without frontmatter."""
    path = SKILLS_DIR / name / "SKILL.md"
    try:
        stat = path.stat()
    except OSError:
        return ""
    return _read_cached(str(path), stat.st_mtime, stat.st_size).strip()


def standards_block(
    names: tuple[str, ...], heading: str = "## Standaarden (verplicht volgen)"
) -> str:
    """Compose one prompt section from several skills; empty when none exist."""
    if tenancy.user_id() != tenancy.LEGACY_ID:
        return (
            heading
            + "\nGebruik uitsluitend feiten en voorkeuren van de huidige gebruiker. Verzin geen beschikbaarheid, woonplaats, opleiding of ervaring. Een CV is precies één A4; een Nederlandse motivatiebrief maximaal 15 regels. Analyseer functie-inhoud, uren, contract, reistijd, belasting en begeleiding zonder onbewezen persoonlijke aannames."
        )
    bodies = [(name, body) for name in names if (body := load_skill(name))]
    if not bodies:
        return ""
    chunks = [f"### {name}\n{body}" for name, body in bodies]
    return f"{heading}\n\n" + "\n\n".join(chunks)
