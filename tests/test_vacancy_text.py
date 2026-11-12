"""The raw vacancy text keeps its own order and headings, also when they are questions."""

from __future__ import annotations

from app import vacancy_text

LISTED = """Wij zoeken een collega voor 32 uur per week.

Wat ga je doen?
- Je bent verantwoordelijk voor de post.
- Je werkt samen met een klein team.

Wat vragen wij?
- MBO-niveau 2 of 3
- Je spreekt goed Nederlands.

Wat bieden wij?
- Een salaris van € 3.374 tot € 4.244 bruto per maand.
- Een goede inwerkperiode."""


def _kinds(text: str) -> dict[str, dict]:
    return {section["kind"]: section for section in vacancy_text.structure(text)["sections"]}


def test_a_heading_asked_as_a_question_still_starts_its_section():
    sections = _kinds(LISTED)
    assert sections["role"]["items"] == ["Je bent verantwoordelijk voor de post.", "Je werkt samen met een klein team."]
    assert sections["ask"]["items"] == ["MBO-niveau 2 of 3", "Je spreekt goed Nederlands."]
    assert sections["offer"]["items"][1] == "Een goede inwerkperiode."
    assert sections["role"]["title"] == "Wat ga je doen"
    # Each list stays under its own heading instead of piling up below every heading.
    assert not any("?" in paragraph for section in sections.values() for paragraph in section["paragraphs"])


def test_a_question_heading_in_flat_text_splits_it_too():
    flat = ("Wij zoeken iemand. Wat ga je doen? Je plant routes en stuurt chauffeurs aan in de regio. "
            "Wat vragen wij? Je hebt een MBO-diploma en rijbewijs B, en je werkt nauwkeurig.")
    sections = _kinds(flat)
    assert sections["role"]["paragraphs"] == ["Je plant routes en stuurt chauffeurs aan in de regio."]
    assert "rijbewijs B" in sections["ask"]["paragraphs"][0]


def test_a_question_in_a_sentence_is_not_mistaken_for_a_heading():
    text = "Ben je benieuwd wat vragen wij stellen in het gesprek? Lees dan verder over de procedure bij ons."
    assert [section["kind"] for section in vacancy_text.structure(text)["sections"]] == ["other"]


def test_the_quick_summary_says_what_the_work_is_not_how_the_page_opens():
    assert vacancy_text.summary(LISTED) == "Je bent verantwoordelijk voor de post. Je werkt samen met een klein team."
    assert vacancy_text.summary("Kort en zonder kopjes: wij zoeken een collega.") == "Kort en zonder kopjes: wij zoeken een collega."
    assert len(vacancy_text.summary("woord " * 200)) == 320


def test_a_line_that_introduces_a_list_keeps_its_colon():
    text = "Wat ga je doen?\nJe taken zijn onder meer:\n- Post sorteren\n- Bezoekers ontvangen bij de balie"
    assert vacancy_text.summary(text) == "Je taken zijn onder meer: Post sorteren. Bezoekers ontvangen bij de balie."
