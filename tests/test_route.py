"""One route per vacancy: the rules, their order, and the thresholds read at call time."""

from __future__ import annotations

from app.scoring import route as routing

SURE = {"fit": 80, "confidence": 0.9}
UNSURE = {"fit": 50, "confidence": 0.4}


def job(**overrides):
    return {"vote": 0, "status": "new", **overrides}


def go(j=None, signals=None, **kw):
    kw.setdefault("fresh", False)
    kw.setdefault("rank", 60.0)
    return routing.route(j or job(), signals, **kw)


def test_rules_in_order():
    assert go(job(vote=-1), SURE).code == "handled"
    assert go(job(status="applied"), SURE).code == "handled"
    assert go(job(), SURE, too_far=True).code == "too_far"
    assert go(job(), UNSURE, fresh=True).code == "fresh"
    assert go(job(), SURE, wanted=True) == routing.Route("escalate", "wanted", "je vroeg om een diepere beoordeling")
    assert go(job(), None).code == "no_signals"
    assert go(job(), {"fit": None, "confidence": 0.9}).code == "no_signals"
    assert go(job(), UNSURE, rank=20.0).code == "low_rank"
    assert go(job(), SURE).code == "sure"
    assert go(job(), UNSURE).code == "unsure"


def test_outcomes_and_words():
    assert go(job(vote=1), SURE).outcome == "excluded"
    assert go(job(), SURE, fresh=True).outcome == "verdict"
    assert go(job(), None).outcome == "wait"
    assert go(job(), SURE).outcome == "act"
    assert go(job(), UNSURE).outcome == "escalate"
    assert set(routing.LABELS) == set(routing.OUTCOMES)


def test_the_person_wins_over_the_rules_but_not_over_a_fresh_verdict():
    assert go(job(), SURE, rank=10.0, wanted=True).outcome == "escalate"
    assert go(job(), None, wanted=True).outcome == "escalate"
    assert go(job(), SURE, fresh=True, wanted=True).outcome == "verdict"


def test_without_a_quick_assessment_to_come_the_matcher_judges_as_before():
    assert go(job(), None, quick=False) == routing.Route("escalate", "no_quick", "geen snelle beoordeling mogelijk")
    assert go(job(), None, quick=False, rank=20.0).code == "low_rank"
    assert go(job(), {"fit": None}, quick=False).outcome == "escalate"
    assert go(job(), SURE, quick=False).code == "sure"


def test_a_missing_rank_skips_the_floor():
    assert go(job(), SURE, rank=None).code == "sure"
    assert go(job(), UNSURE, rank=None).code == "unsure"


def test_thresholds_are_read_when_asked(monkeypatch):
    monkeypatch.setenv("SYSTEMONE_CONFIDENCE", "0.95")
    monkeypatch.setenv("MATCH_MIN_RANK", "70")
    monkeypatch.setenv("MATCH_SAMPLE", "0")
    assert routing.read_thresholds() == routing.Thresholds(min_rank=70.0, confidence=0.95, sample=0)
    assert go(job(), SURE, rank=80.0).code == "unsure"
    assert go(job(), SURE, rank=65.0).code == "low_rank"
    monkeypatch.setenv("SYSTEMONE_CONFIDENCE", "nonsense")
    assert routing.read_thresholds().confidence == 0.75


def test_certain_needs_a_fit_and_a_number():
    assert routing.certain(SURE, routing.Thresholds())
    assert not routing.certain(UNSURE, routing.Thresholds())
    assert not routing.certain({"confidence": 0.99}, routing.Thresholds())
    assert not routing.certain({"fit": 80, "confidence": "?"}, routing.Thresholds())
    assert not routing.certain(None, routing.Thresholds())
