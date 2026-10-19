from __future__ import annotations

from app.profile import public_excerpt, split_sections

MD = """# Profiel Owner

## Kern
Kern feit.

## Werkvoorkeuren
Buiten en mensen.

## Open vragen
Onzeker.

## Privé (niet openbaar gebruiken)
Gevoelig detail.

## Zoekprofiel
Groen en buiten.
"""


def test_split_sections_keeps_preamble_and_headings():
    sections = split_sections(MD)
    headings = [section["heading"] for section in sections]
    assert headings == [
        "",
        "Kern",
        "Werkvoorkeuren",
        "Open vragen",
        "Privé (niet openbaar gebruiken)",
        "Zoekprofiel",
    ]
    assert "Profiel Owner" in sections[0]["text"]


def test_public_excerpt_excludes_private_and_open_questions():
    text = public_excerpt(MD, 6000)
    assert "Kern feit" in text
    assert "Buiten en mensen" in text
    assert "Groen en buiten" in text
    assert "Gevoelig detail" not in text
    assert "Onzeker" not in text


def test_public_excerpt_respects_character_budget():
    markdown = "# Profiel\n\n## Kern\n" + "a" * 1000 + "\n\n## Zoekprofiel\n" + "b" * 1000
    text = public_excerpt(markdown, 700)
    assert "b" * 100 not in text
    assert "afgekapt" in text
    assert len(text) < 900
