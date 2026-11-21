"""Wishes the deterministic ranker handles: changing one is free and instant.

These never reach a prompt or a verdict digest. A vacancy that does not mention
something is never penalised for it.
"""

from __future__ import annotations

import re

_AMOUNT = re.compile(r"(?:€|eur(?:o)?)\s*(\d{1,3}(?:[.,]\d{3})+|\d{3,6})(?:[.,]\d{2})?")
_FULL_REMOTE = re.compile(r"\b(?:volledig (?:remote|thuis)|100% (?:remote|thuis)|fully remote|remote[- ]first)\b")
_HYBRID = re.compile(r"\b(?:hybride?|deels thuis|thuiswerk\w*|gedeeltelijk thuis|remote)\b")
_ONSITE = re.compile(r"\b(?:geen thuiswerk\w*|alleen op (?:kantoor|locatie)|volledig op (?:kantoor|locatie))\b")
_CONTRACTS = {
    "vast": re.compile(r"\b(?:vast (?:contract|dienstverband)|vaste (?:aanstelling|baan|contract)|permanent (?:contract|position))\b"),
    "tijdelijk": re.compile(r"\b(?:tijdelijk\w*|jaarcontract|bepaalde tijd|detachering|fixed[- ]term|temporary)\b"),
    "freelance": re.compile(r"\b(?:freelance\w*|zzp\w*|interim\w*|contractor)\b"),
    "stage": re.compile(r"\b(?:stage\w*|bijbaan|traineeship)\b"),
}
_SENIORITY = {
    "senior": re.compile(r"\b(?:senior|sr\.?|lead|principal)\b"),
    "medior": re.compile(r"\b(?:medior)\b"),
    "junior": re.compile(r"\b(?:junior|jr\.?|starter|trainee)\b"),
}
YEAR_TO_MONTH = 12.96  # Dutch yearly pay usually includes holiday pay and a thirteenth month
# "€ 3.000 - 4.000" and "€ 3000 tot 4000": the upper amount often has no euro sign of its own.
_RANGE_END = re.compile(
    r"\s*(?:,-)?\s*(?:bruto\s+)?(?:-|–|tot en met|tot maximaal|tot|t/m|en maximaal)\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,6})(?![\d%])"
)
# A training budget or a bonus is money, but not what the job pays a month.
_NOT_PAY_BEFORE = re.compile(
    r"(?:budget|bonus|reiskostenvergoeding|thuiswerkvergoeding|onkostenvergoeding|premie|korting|subsidie|omzet|tegoed)"
    r"\w*[^\w€.]+(?:(?!en\b|plus\b)\w+[^\w€.]+){0,3}$"
)
_PER_YEAR_AFTER = re.compile(r"(?:[.,]\d{2}|,-)?\s*(?:bruto\s+|netto\s+)?(?:per jaar|p/j|jaarlijks|eenmalig)")

HOURS = re.compile(r"\b(\d{1,2})(?:\s*(?:-|–|tot|to|à)\s*(\d{1,2}))?\s*(?:uur|uren|hours)\b", re.I)
_WEEK_AFTER = re.compile(r"\s*(?:per\s+(?:werk)?week|p\.?\s?/?\s?w\b|/\s*week|in de week|per wk)", re.I)
# "bij 36 uur" and "op basis van 36 uur" say what a salary is counted on, not what the job is.
_NOT_JOB_HOURS_BEFORE = re.compile(
    r"(?:binnen|na|elke|iedere|om de|per|bij|basis van|o\.b\.v\.|gebaseerd op)\s+(?:een\s+)?$", re.I
)
_NOT_JOB_HOURS_AFTER = re.compile(
    r"\s*(?:per\s+(?:dag|etmaal|maand|jaar)|bereikbaar|beschikbaar|service|reactie|open|van tevoren|vooraf|training|opleiding|cursus)",
    re.I,
)


def monthly_salary(text: str) -> float | None:
    """Highest plausible monthly gross pay named in a vacancy, if any."""
    best = None
    for match in _AMOUNT.finditer(text):
        if _NOT_PAY_BEFORE.search(text[max(0, match.start() - 60) : match.start()]):
            continue
        amounts = [match.group(1)]
        upper = _RANGE_END.match(text, match.end())
        if upper:
            amounts.append(upper.group(1))
        yearly_small = bool(_PER_YEAR_AFTER.match(text, (upper or match).end()))
        for raw in amounts:
            amount = float(re.sub(r"[.,]", "", raw))
            if 20000 <= amount <= 300000:
                amount /= YEAR_TO_MONTH
            elif yearly_small:
                continue  # "€ 2.000 per jaar" is an allowance
            if 1500 <= amount <= 20000:
                best = max(best or 0.0, amount)
    return best


def working_hours(text: str) -> re.Match | None:
    """The hours of the job itself: the first figure said to be per week, else the first that
    can be. Not "binnen 24 uur reactie", "24 uur per dag bereikbaar" or "bij 36 uur"."""
    possible = None
    for match in HOURS.finditer(text):
        after = text[match.end() : match.end() + 24]
        before = text[max(0, match.start() - 20) : match.start()]
        highest = max(int(number) for number in match.groups() if number)
        if highest > 40 or _NOT_JOB_HOURS_BEFORE.search(before) or _NOT_JOB_HOURS_AFTER.match(after):
            continue
        if _WEEK_AFTER.match(after):
            return match
        possible = possible or match
    return possible


def hours_range(text: str) -> tuple[int, int] | None:
    """(fewest, most) hours a week the vacancy offers."""
    match = working_hours(text)
    if not match:
        return None
    numbers = sorted(int(number) for number in match.groups() if number)
    return numbers[0], numbers[-1]


def contract_types(text: str) -> set[str]:
    return {name for name, pattern in _CONTRACTS.items() if pattern.search(text)}


def seniority_of(title: str) -> str | None:
    lowered = (title or "").lower()
    return next((name for name, pattern in _SENIORITY.items() if pattern.search(lowered)), None)


def adjust(text: str, title: str, wishes: dict | None) -> tuple[float, list[str]]:
    wishes = wishes or {}
    delta = 0.0
    notes: list[str] = []

    minimum = wishes.get("salary_min")
    if minimum:
        pay = monthly_salary(text)
        if pay is not None and pay < minimum:
            delta -= 12.0
            notes.append("salaris onder je wens")
        elif pay is not None:
            delta += 3.0
            notes.append("salaris past")

    remote = wishes.get("remote") or "any"
    if remote in ("remote", "hybrid"):
        if _ONSITE.search(text):
            delta -= 8.0
            notes.append("geen thuiswerk")
        elif _FULL_REMOTE.search(text):
            delta += 6.0
            notes.append("volledig thuis mogelijk")
        elif _HYBRID.search(text):
            delta += 6.0 if remote == "hybrid" else 2.0
            notes.append("deels thuis mogelijk")

    wanted = set(wishes.get("contracts") or [])
    offered = contract_types(text) if wanted else set()
    if offered:
        if offered & wanted:
            delta += 3.0
        else:
            delta -= 6.0
            notes.append("ander soort contract")

    levels = set(wishes.get("seniority") or [])
    level = seniority_of(title) if levels else None
    if level:
        if level in levels:
            delta += 4.0
        else:
            delta -= 8.0
            notes.append(f"niveau {level}")
    return delta, notes


def remote_kind(text: str) -> str:
    if _ONSITE.search(text):
        return "Op locatie"
    if _FULL_REMOTE.search(text):
        return "Volledig thuis"
    return "Deels thuis" if _HYBRID.search(text) else ""
