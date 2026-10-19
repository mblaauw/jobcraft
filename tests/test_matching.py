from __future__ import annotations

from app.matching import rule_score


def preferences():
    return {
        "tracks": [
            {"id": "groen", "weight": 0.6, "keywords": ["planten", "groen"]},
            {"id": "locatie", "weight": 0.4, "keywords": ["locatie", "facilitair"]},
        ],
        "likes": [{"tag": "buiten", "weight": 2}],
        "dislikes": [{"tag": "callcenter", "weight": -3}],
        "keywords": {"boost": ["buitendienst"], "veto": ["sales", "targets"]},
        "home": {"label": "Amsterdam"},
        "hard_filters": {"no_phone_main": True, "hours_min": 24, "hours_max": 32},
    }


def job(title="Medewerker groen", description="Buiten werken in Amsterdam, 24 uur."):
    return {"title": title, "company": "", "description": description, "source_id": "test"}


def test_keyword_and_amsterdam_boost():
    score, notes = rule_score(job(), preferences())
    assert score > 40
    assert any("Amsterdam" in note for note in notes)
    assert any("24-32" in note for note in notes)


def test_veto_words_reduce_score():
    good, _ = rule_score(job(), preferences())
    bad, notes = rule_score(job(description="Sales baan met targets, fulltime."), preferences())
    assert bad < good
    assert any("veto" in note for note in notes)


def test_score_is_clamped():
    high, _ = rule_score(
        job(description="planten groen buiten locatie facilitair buitendienst Amsterdam 24 uur"),
        preferences(),
    )
    assert 0 <= high <= 100
    low, _ = rule_score(job(description="sales targets callcenter callcenter"), preferences())
    assert 0 <= low <= 100
