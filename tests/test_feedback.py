from __future__ import annotations

from app import feedback


def _votes():
    return {
        "up": [
            {
                "id": 1,
                "title": "Plantenverzorger buitendienst",
                "company": "Groen BV",
                "source_id": "greenjobs",
                "description": "planten verzorgen en buiten werken",
                "vote": 1,
            }
        ],
        "down": [
            {
                "id": 2,
                "title": "Callcenter medewerker sales",
                "company": "Bel BV",
                "source_id": "jobbird",
                "description": "bellen sales targets telefonische acquisitie",
                "vote": -1,
            }
        ],
    }


def test_rebuild_and_rule_adjust(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(feedback.store, "list_votes", _votes)

    data = feedback.rebuild()
    assert data["counts"] == {"up": 1, "down": 1}
    liked = {item["keyword"] for item in data["liked_keywords"]}
    disliked = {item["keyword"] for item in data["disliked_keywords"]}
    assert liked & {"planten", "buitendienst", "verzorgen", "buiten"}
    assert disliked & {"callcenter", "sales", "bellen", "targets"}

    positive, positive_notes = feedback.rule_adjust(
        "planten verzorgen buiten", "Groen BV", "greenjobs", data
    )
    assert positive > 0 and positive_notes

    negative, negative_notes = feedback.rule_adjust(
        "bellen sales targets", "Bel BV", "jobbird", data
    )
    assert negative < 0 and negative_notes


def test_prompt_block_contains_feedback(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(feedback.store, "list_votes", _votes)
    data = feedback.rebuild()
    block = feedback.prompt_block(data)
    assert "Feedback van de gebruiker" in block
    assert "Plantenverzorger buitendienst" in block
    assert "Callcenter medewerker sales" in block


def test_empty_feedback_is_neutral(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(feedback.store, "list_votes", lambda: {"up": [], "down": []})
    data = feedback.rebuild()
    delta, notes = feedback.rule_adjust("wat tekst", None, None, data)
    assert delta == 0 and notes == []
    assert feedback.prompt_block(data) == ""
