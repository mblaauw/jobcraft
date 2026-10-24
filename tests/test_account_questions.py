import copy
import json

import pytest

from app import matching, profile, store, systemone, tenancy
from tests.factories import add_job


def test_tracks_change_only_their_owners_questions_and_keys():
    original = copy.deepcopy(systemone.QUESTIONS)
    job = {"content_hash": "fixture"}
    with tenancy.scope({"id": "b", "username": "b"}):
        other_key = systemone.signals_key(job, "public")
    with tenancy.scope({"id": "a", "username": "a"}):
        profile.write_preferences(
            {"tracks": [{"id": "test", "name": "Techniek", "keywords": ["installatie"]}]}
        )
        questions = systemone.questions_for(profile.read_preferences())
        assert set(questions["category"]["criteria"]) == {"test", "overig"}
        assert "installatie" in questions["category"]["criteria"]["test"]
        before = systemone.signals_key(job, "public")
        profile.write_preferences(
            {"tracks": [{"id": "test", "name": "Onderwijs", "keywords": ["lesgeven"]}]}
        )
        assert systemone.signals_key(job, "public") != before
        assert len([q for q in questions.values() if q["type"] == "noul"]) == 9
    with tenancy.scope({"id": "b", "username": "b"}):
        assert systemone.signals_key(job, "public") == other_key
        assert len(systemone.questions_for({})["category"]["criteria"]) == 11
    assert systemone.QUESTIONS == original


def test_question_contract_hash_includes_wording_and_excludes_private_bookkeeping():
    questions = systemone.questions_for({})
    changed = copy.deepcopy(questions)
    changed["fit"]["instructions"] += " Additional rule."
    assert systemone.question_version(changed) != systemone.question_version(questions)
    job = {"content_hash": "fixture"}
    a = {"notes": "private-a", "updated_at": "one", "home": {"lat": 1, "lon": 2}}
    b = {"notes": "private-b", "updated_at": "two", "home": {"lat": 3, "lon": 4}}
    assert systemone.signals_key(job, "public", preferences=a) == systemone.signals_key(
        job, "public", preferences=b
    )


def test_duplicate_or_reserved_tracks_are_rejected():
    for ids in [("same", "same"), ("overig", "other")]:
        with pytest.raises(ValueError):
            profile.write_preferences({"tracks": [{"id": key, "name": key} for key in ids]})


def test_assembled_prompts_exclude_private_preference_fields():
    job = store.get_job(add_job())
    preferences = {
        "notes": "PRIVATE_CANARY",
        "home": {"lat": 12.345678, "lon": 23.456789},
        "likes": [{"tag": "samenwerken", "evidence": "PRIVATE_EVIDENCE"}],
        "hard_filters": {"unknown": "PRIVATE_FILTER"},
    }
    state = systemone.compose_state(job, "PUBLIC", preferences=preferences)
    prompt = matching.verdict_prompt(job, preferences, "PUBLIC")
    for text in (state, prompt):
        for secret in (
            "PRIVATE_CANARY",
            "PRIVATE_EVIDENCE",
            "PRIVATE_FILTER",
            "12.345678",
            "23.456789",
        ):
            assert secret not in text
        assert "PUBLIC" in text and "samenwerken" in text


@pytest.mark.parametrize(
    ("flag", "signal", "probability", "delta"),
    [
        ("no_phone_main", "phone_sales_main", 0.9, -15),
        ("no_structural_evenings", "evening_weekend", 0.9, -12),
        ("no_heavy_physical", "physically_heavy", 0.9, -12),
        ("day_work_only", "day_hours", 0.1, -10),
        ("wants_guidance", "guidance", 0.9, 4),
    ],
)
def test_hard_filter_signals_only_apply_when_enabled(flag, signal, probability, delta):
    job = dict(store.get_job(add_job()))
    job["typed_signals"] = json.dumps({"noul": {signal: probability}})
    neutral, _ = matching.rule_score(job, {"hard_filters": {}})
    filtered, _ = matching.rule_score(job, {"hard_filters": {flag: True}})
    assert filtered == neutral + delta


def test_neutral_defaults_do_not_replace_stored_restrictive_flags():
    assert not profile.Preferences().hard_filters.no_phone_main
    profile.write_preferences(
        {"hard_filters": {"no_phone_main": True, "no_structural_evenings": True}}
    )
    assert profile.read_preferences()["hard_filters"]["no_phone_main"] is True


def test_matching_stores_the_question_snapshot_that_was_actually_sent(monkeypatch):
    profile.write_preferences({"tracks": [{"id": "old", "name": "Old", "keywords": []}]})
    job_id = add_job()
    prefs = profile.read_preferences()
    questions = systemone.questions_for(prefs)
    expected = systemone.signals_key(
        store.get_job(job_id), "", preferences=prefs, questions=questions
    )

    def decide(state, *, questions):
        assert "old" in questions["category"]["criteria"]
        profile.write_preferences({"tracks": [{"id": "new", "name": "New"}]})
        return {"answers": {}}, {"input_tokens": 1}, 1

    monkeypatch.setattr(systemone, "enabled", lambda: True)
    monkeypatch.setattr(systemone, "decide", decide)
    monkeypatch.setattr(matching.ranking, "rerank_all", lambda *args: 0)
    monkeypatch.setattr(matching.ranking, "escalation_candidates", lambda *args: [])
    monkeypatch.setattr(matching.ranking, "queue_depth", lambda *args: 0)
    matching.match_jobs(None)
    row = store.get_job(job_id)
    assert row["signals_key"] == expected
    assert row["signals_key"] != systemone.signals_key(row, "")
    assert json.loads(row["typed_signals"])["version"] == systemone.question_version(questions)
    assert store.usage_total()["calls"] == 1
