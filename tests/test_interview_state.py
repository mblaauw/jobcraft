from __future__ import annotations

from app import interview_state


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(interview_state, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(interview_state, "PLAN_FILE", tmp_path / "plan.md")
    monkeypatch.setattr(interview_state, "ANSWER_LOG_FILE", tmp_path / "answer-log.md")


def test_record_answer_marks_topic_after_three_signals(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = interview_state.ensure_state(seed_from_log=False)
    assert len(state["topics"]) == len(interview_state.TOPIC_SPECS)

    interview_state.record_answer(
        "Wat geeft je energie?", "Ik werk graag buiten en 24 uur per week."
    )
    summary = interview_state.summary()
    assert summary["question_count"] == 1
    uren = next(topic for topic in summary["topics"] if topic["id"] == "uren_ritme")
    assert uren["status"] == "open"  # one signal is not enough

    interview_state.record_answer("Hoeveel uur?", "32 uur per week is goed.")
    interview_state.record_answer("En je rooster?", "Dagdienst, 32 uur per week.")
    uren = next(
        topic for topic in interview_state.summary()["topics"] if topic["id"] == "uren_ritme"
    )
    assert uren["status"] == "covered"
    assert "Uren, ritme en voorspelbaarheid" not in interview_state.open_topics()
    assert not (tmp_path / "plan.md").exists()


def test_manual_toggle_wins_over_heuristic(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    interview_state.ensure_state(seed_from_log=False)
    interview_state.toggle("reistijd", "covered")
    summary = interview_state.summary()
    reistijd = next(topic for topic in summary["topics"] if topic["id"] == "reistijd")
    assert reistijd["status"] == "covered" and reistijd["manual"] is True
    assert "Reistijd, fiets/OV, bedrijfsauto" not in interview_state.open_topics()

    interview_state.toggle("reistijd", "open")
    assert "Reistijd, fiets/OV, bedrijfsauto" in interview_state.open_topics()
    assert not (tmp_path / "plan.md").exists()


def test_unknown_topic_is_rejected(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    interview_state.ensure_state(seed_from_log=False)
    try:
        interview_state.toggle("does-not-exist", "covered")
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("unknown topic should raise")


def test_generic_words_do_not_mark_topics_covered():
    interview_state.ensure_state(seed_from_log=False)
    for _ in range(3):
        interview_state.record_answer("", "Goed, fijn, leuk, druk, zin.")
    assert all(topic["status"] == "open" for topic in interview_state.summary()["topics"])
