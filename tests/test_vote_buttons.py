"""The same question gets its answers in the same place on every screen that asks it."""

from __future__ import annotations

import re

from app import config

SCREENS = {
    "_judge.html": ("ja", "nee"),  # Vandaag: vijf voor vandaag
    "jobs_swipe.html": ("up", "down"),  # Vacatures: één voor één
    "start_swipe.html": ("ja", "nee"),  # Snelle start
}


def test_yes_comes_before_no_with_the_same_words_everywhere():
    for name, (yes, no) in SCREENS.items():
        html = (config.BASE_DIR / "app" / "templates" / name).read_text()
        buttons = re.findall(r'<button[^>]*value="(\w+)"[^>]*>(?:\{\{[^}]*\}\})?([^<]*)</button>', html)
        answers = {value: label.strip() for value, label in buttons}
        assert [value for value, _ in buttons if value in (yes, no)] == [yes, no], name
        assert (answers[yes], answers[no]) == ("Ja", "Nee"), name
