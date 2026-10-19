from __future__ import annotations

from app import matching, ranking


def _prefs():
    return {
        "tracks": [],
        "likes": [],
        "dislikes": [],
        "keywords": {},
        "hard_filters": {},
    }


def _job(**overrides):
    job = {
        "id": 1,
        "title": "Medewerker groen",
        "company": "Gemeente",
        "description": "Buiten werken in Amsterdam, 24 uur, dagdienst.",
        "source_id": "test",
        "location": "Amsterdam",
        "lat": None,
        "lon": None,
        "match_score": None,
        "profile_hash": None,
        "typed_signals": None,
        "rank_score": None,
        "content_hash": "job-hash",
    }
    job.update(overrides)
    return job


def test_effective_fit_shrinks_with_low_confidence():
    assert ranking.effective_fit({"fit": 100, "confidence": 1.0}) == 100
    assert ranking.effective_fit({"fit": 100, "confidence": 0.5}) == 75
    assert ranking.effective_fit({"fit": 40, "confidence": 0.0}) == 50
    assert ranking.effective_fit(None) is None


def test_rank_uses_jev_signals():
    rule, _ = matching.rule_score(_job(), _prefs(), {"counts": {"up": 0, "down": 0}})
    rank, parts = ranking.rank_for(
        _job(),
        _prefs(),
        {"counts": {"up": 0, "down": 0}},
        {"fit": 100, "confidence": 1.0},
    )
    assert rank > rule
    assert parts["fit"] == 100
    assert parts["llm"] is None


def test_fresh_llm_verdict_is_blended_stale_is_ignored(monkeypatch):
    prefs = _prefs()
    monkeypatch.setattr(matching.profile, "read_public_excerpt", lambda *_: "profile")
    digest = matching.current_digest(prefs)
    fresh = _job(
        match_score=90, profile_hash=matching.job_match_digest(digest, _job(match_score=90))
    )
    rank_fresh, parts_fresh = ranking.rank_for(fresh, prefs, {}, None, digest)
    assert parts_fresh["llm"] == 90
    stale = _job(match_score=90, profile_hash="stale")
    _, parts_stale = ranking.rank_for(stale, prefs, {}, None, digest)
    assert parts_stale["llm"] is None
    assert rank_fresh > 0


def test_digest_tracks_public_profile_and_prompt_version(monkeypatch):
    prefs = _prefs()
    monkeypatch.setattr(matching.profile, "read_public_excerpt", lambda *_: "first profile")
    first = matching.current_digest(prefs)
    monkeypatch.setattr(matching.profile, "read_public_excerpt", lambda *_: "changed profile")
    changed = matching.current_digest(prefs)
    assert changed != first
    monkeypatch.setattr(matching, "VERDICT_PROMPT_VERSION", "next-prompt-version")
    assert matching.current_digest(prefs) != changed


def test_digest_does_not_depend_on_feedback(monkeypatch):
    prefs = _prefs()
    monkeypatch.setattr(matching.profile, "read_public_excerpt", lambda *_: "profile")
    first = matching.current_digest(prefs)

    def explode():
        raise AssertionError("feedback must not affect the verdict digest")

    monkeypatch.setattr(matching.feedback, "signature", explode)
    assert matching.current_digest(prefs) == first
