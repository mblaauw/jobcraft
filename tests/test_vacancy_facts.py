"""What a vacancy says about pay, hours and contract, read the way a person would."""

from __future__ import annotations

import pytest

from app import cv_text, vacancy_text
from app.scoring import reasons, rules, wishes


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("Salaris tussen € 3.100 en € 4.200 bruto per maand", "€ 3.100 – € 4.200"),
        ("salaris van minimaal €3.012 en maximaal €4.312 bruto", "€3.012 – €4.312"),
        ("€ 3.100,- tot € 4.200,- bruto", "€ 3.100 – € 4.200"),
        ("Je krijgt € 500 en € 20 per gewerkte dag", "€ 500"),  # "en" without "tussen" is no range
        ("max. € 4.200 (schaal 8)", "€ 4.200"),
        ("Opleidingsbudget van € 2.000 per jaar. Salaris € 3.500", "€ 3.500"),  # a budget is no salary
    ],
)
def test_a_salary_range_is_shown_whole(text, shown):
    assert dict(vacancy_text.facts_of(text))["Salaris"] == shown


@pytest.mark.parametrize(
    ("text", "hours"),
    [
        ("Wij zijn 24 uur per dag bereikbaar. Een functie van 28 tot 32 uur.", "28 tot 32 uur"),
        ("Reactie binnen 48 uur. Contract voor 24-32 uur per week", "24-32 uur"),
        ("Salaris bij 36 uur per week: € 3.000. Je werkt 32 uur.", "32 uur"),
    ],
)
def test_the_hours_fact_is_the_working_week(text, hours):
    assert dict(vacancy_text.facts_of(text))["Uren"] == hours


def test_a_bonus_is_not_shown_as_the_salary():
    assert "Salaris" not in dict(vacancy_text.facts_of("Een bonus van € 500 bij indiensttreding."))


def test_no_hours_fact_from_a_response_time():
    assert "Uren" not in dict(vacancy_text.facts_of("Je hoort binnen 48 uur van ons."))
    assert wishes.hours_range("een werkweek van 45 uur") == (45, 45)  # long weeks exist and count against a wish


@pytest.mark.parametrize(
    ("text", "pay"),
    [
        ("€ 3.000 - 4.000 per maand", 4000),
        ("€ 3000 tot 4000", 4000),
        ("opleidingsbudget van € 2.500 per jaar en een salaris van € 2.900", 2900),
        ("een bonus van € 5.000", None),
        ("€ 2.000 per jaar", None),
        ("een marktconforme vergoeding van € 3.200 - € 4.000 bruto per maand", 4000),
        ("een ikb van 17,05% en een salaris van € 3.345", 3345),
    ],
)
def test_monthly_salary_reads_ranges_and_skips_allowances(text, pay):
    assert wishes.monthly_salary(text) == pay


def test_hours_that_overlap_the_wish_count_as_fitting():
    preferences = {"hard_filters": {"hours_min": 24, "hours_max": 32}}

    def score(text: str) -> float:
        return rules.rule_score({"title": "", "company": "", "description": "", "search_text": text}, preferences)[0]

    assert score("een functie van 32 tot 40 uur") == score("een functie van 28 uur") > score("een functie van 36 tot 40 uur")
    assert score("wij zijn 24 uur per dag bereikbaar, de functie is 40 uur per week") < score("geen uren genoemd")


def test_a_dislike_counts_whatever_sign_its_weight_has():
    job = {"title": "", "company": "", "description": "", "search_text": "werken in een callcenter"}
    plain = rules.rule_score(job, {})[0]
    for weight in (1.0, -1.0):
        assert rules.rule_score(job, {"dislikes": [{"tag": "callcenter", "weight": weight}]})[0] == plain - 2.0


def test_temporary_with_a_view_to_permanent_is_one_chip():
    chips = reasons.facts({"search_text": "je start met een jaarcontract met uitzicht op een vast contract"})
    assert chips == ["Tijdelijk, uitzicht op vast"]


def test_a_profile_title_is_not_a_first_name():
    assert cv_text.contact_of("# Profiel Sam Jansen\n\n## Kern\nSam Jansen (38) is")["name"] == "Sam Jansen"
    assert "name" not in cv_text.contact_of("# Profiel\n\n## Kern\nTekst")
