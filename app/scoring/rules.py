"""Deterministic vacancy rules; no matching-orchestrator dependency."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from .. import feedback
from . import signals as ranking


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower())



def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default



def rule_score(
    job, preferences: dict, feedback_data: dict | None = None
) -> tuple[float, list[str]]:
    """Fast deterministic pre-score in 0..100 with short reason notes."""
    text = _row_get(job, "search_text") or _norm(
        " ".join([job["title"] or "", job["company"] or "", job["description"] or ""])
    )
    score = 40.0
    notes: list[str] = []

    for track in preferences.get("tracks", []):
        keywords = [kw for kw in track.get("keywords", []) if kw]
        hits = sum(1 for kw in keywords if kw.lower() in text)
        if hits:
            score += min(5, hits) * (2.0 * float(track.get("weight", 1.0)))
            notes.append(f"track {track.get('id')}: {hits} keyword(s)")

    for item in preferences.get("likes", []):
        tag = (item.get("tag") or "").lower()
        if tag and tag in text:
            score += min(3.0, float(item.get("weight", 1.0))) * 1.5

    for item in preferences.get("dislikes", []):
        tag = (item.get("tag") or "").lower()
        if tag and tag in text:
            score += min(0.0, float(item.get("weight", -1.0))) * 2.0
            notes.append(f"dislike: {tag}")

    for keyword in preferences.get("keywords", {}).get("boost", []):
        if keyword and keyword.lower() in text:
            score += 1.0
    for keyword in preferences.get("keywords", {}).get("veto", []):
        if keyword and keyword.lower() in text:
            score -= 12.0
            notes.append(f"veto: {keyword}")

    home = (preferences.get("home") or {}).get("label") or ""
    home_label = _norm(home)
    if home_label and home_label != "nog niet ingesteld" and home_label in text:
        score += 6.0
        notes.append(f"{home} vermeld")
    hours = preferences.get("hard_filters", {})
    try:
        hours_min = float(hours.get("hours_min"))
        hours_max = float(hours.get("hours_max"))
    except (TypeError, ValueError):
        hours_min = hours_max = 0.0
    match = re.search(r"\b(\d{1,2})\s*(?:uur|hours?)\b", text)
    if match and hours_min and hours_max:
        offered = float(match.group(1))
        if hours_min <= offered <= hours_max:
            score += 4.0
            notes.append(f"{int(hours_min)}-{int(hours_max)} uur vermeld")
        elif offered > hours_max:
            score -= 3.0
    filters = preferences.get("hard_filters", {})
    noul = (ranking.signals_of(job) or {}).get("noul") or {}

    def probability(key):
        try:
            return float(noul[key])
        except (KeyError, TypeError, ValueError):
            return None

    phone = probability("phone_sales_main")
    if filters.get("no_phone_main"):
        if phone is not None and phone >= 0.75:
            score -= 15.0
        elif phone is None and any(
            word in text for word in hard_filter_keywords().get("no_phone_main", [])
        ):
            score -= 10.0
    if filters.get("no_structural_evenings") and (probability("evening_weekend") or 0) >= 0.75:
        score -= 12.0
    if filters.get("no_heavy_physical") and (probability("physically_heavy") or 0) >= 0.75:
        score -= 12.0
    if filters.get("day_work_only"):
        day = probability("day_hours")
        if day is not None and day <= 0.25:
            score -= 10.0
    if filters.get("wants_guidance") and (probability("guidance") or 0) >= 0.75:
        score += 4.0

    travel = ranking.travel_of(job)
    if travel.get("in_range") is False and not travel.get("auto"):
        score -= 25.0
        notes.append(f"te ver: {travel['label']}")
    elif travel.get("in_range") is True:
        score += 4.0
        notes.append("binnen reisbereik (schatting)")
    elif travel.get("auto"):
        notes.append("mobiel werk met auto")

    fb_delta, fb_notes = feedback.rule_adjust(
        text, _row_get(job, "company"), _row_get(job, "source_id"), feedback_data
    )
    score += fb_delta
    notes.extend(fb_notes)

    return max(0.0, min(100.0, score)), notes



@lru_cache(maxsize=1)
def hard_filter_keywords() -> dict:
    return yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "data" / "hard_filter_keywords.yaml").read_text()
    )

