"""The same control means the same thing in the same place on every screen."""

from __future__ import annotations

import re

from test_jobs_page import client  # noqa: F401

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


def test_only_the_deepest_menu_item_marks_the_page_you_are_on(client):  # noqa: F811
    from test_auth import csrf_of  # noqa: F401
    from test_auth_events import _bootstrap_password, _full_app_client

    admin = _full_app_client("admin", _bootstrap_password("admin"))
    for path, label in (("/admin/ai", "AI"), ("/admin", "Overzicht"), ("/admin/instellingen", "Instellingen")):
        current = re.findall(r'aria-current="page">(?:<svg.*?</svg>)?([^<]+)<', admin.get(path).text, re.S)
        assert set(current) == {label}, (path, current)
