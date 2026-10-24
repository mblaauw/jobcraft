import ast
from pathlib import Path

import pytest

from app import matching, ranking
from app.scoring import digests
from app.scoring.weights import Weights, read_weights


def test_default_digest_remains_identical_and_version_seam_survives(monkeypatch):
    assert matching.current_digest({}, "PUBLIC") == digests.current_digest({}, "PUBLIC")
    before = matching.current_digest({}, "PUBLIC")
    monkeypatch.setattr(matching, "VERDICT_PROMPT_VERSION", "changed")
    assert matching.current_digest({}, "PUBLIC") != before


def test_ranker_no_longer_imports_matching():
    tree = ast.parse(Path(ranking.__file__).read_text())
    assert not any(
        (isinstance(n, ast.ImportFrom) and (n.module or "").endswith("matching"))
        or (isinstance(n, ast.ImportFrom) and any(a.name == "matching" for a in n.names))
        for n in ast.walk(tree)
    )


@pytest.mark.parametrize(
    "values", [(-1, 1, 1), (0, 0, 0), (float("nan"), 1, 1), (1, float("inf"), 1)]
)
def test_invalid_weights_rejected(values):
    with pytest.raises(ValueError):
        Weights(*values)


def test_configurable_weights_leave_paid_digest_unchanged(monkeypatch):
    before = matching.current_digest({}, "PUBLIC")
    assert read_weights() == Weights(0.35, 0.40, 0.25)
    monkeypatch.setenv("MATCH_WEIGHT_RULE", ".8")
    assert read_weights().rule == 0.8
    assert matching.current_digest({}, "PUBLIC") == before


def test_missing_weighted_components_fall_back_to_rules(monkeypatch):
    monkeypatch.setenv("MATCH_WEIGHT_RULE", "0")
    monkeypatch.setenv("MATCH_WEIGHT_FIT", "1")
    monkeypatch.setenv("MATCH_WEIGHT_LLM", "0")
    monkeypatch.setattr(ranking, "rule_score", lambda *args: (73.0, []))
    assert ranking.rank_for({}, {}, {})[0] == 73


def test_large_finite_weights_do_not_overflow(monkeypatch):
    monkeypatch.setenv("MATCH_WEIGHT_RULE", "1e308")
    monkeypatch.setenv("MATCH_WEIGHT_FIT", "0")
    monkeypatch.setenv("MATCH_WEIGHT_LLM", "0")
    monkeypatch.setattr(ranking, "rule_score", lambda *args: (40.0, []))
    assert ranking.rank_for({}, {}, {})[0] == 40
    assert matching.hard_filter_keywords()["no_phone_main"]
