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


def test_private_sections_stay_out_however_the_heading_is_typed():
    markdown = (
        "# Profiel\n\n## Kern\nKern feit.\n\n### Privé\nGeheim onder een kopje.\n\n### Verder\nOpenbaar onder een kopje.\n\n"
        "## Prive\nGeheim zonder accent.\n\n## privé:\nGeheim klein geschreven.\n\n## Open Vragen\nGeheim open.\n\n"
        "## Zoekprofiel\nGroen en buiten.\n"
    )
    text = public_excerpt(markdown, 6000)
    assert "Geheim" not in text
    assert "Kern feit" in text and "Openbaar onder een kopje" in text and "Groen en buiten" in text


def test_document_excerpt_keeps_small_sections_next_to_a_long_one():
    markdown = "# Profiel\n\n## Werkervaring\n" + "Lang stuk tekst. " * 900 + "\n\n## Opleiding\nMbo 4.\n\n## Vaardigheden\nExcel.\n"
    assert "Mbo 4" not in public_excerpt(markdown, 6000)  # the matching excerpt is unchanged
    text = public_excerpt(markdown, 6000, fill=True)
    assert "Lang stuk tekst." in text and "Mbo 4." in text and "Excel." in text
    assert "afgekapt" in text and len(text) < 6200
