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


def monthly_salary(text: str) -> float | None:
    """Highest plausible monthly gross pay named in a vacancy, if any."""
    best = None
    for match in _AMOUNT.finditer(text):
        amount = float(re.sub(r"[.,]", "", match.group(1)))
        if 20000 <= amount <= 300000:
            amount /= YEAR_TO_MONTH
        if 1500 <= amount <= 20000:
            best = max(best or 0.0, amount)
    return best


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
