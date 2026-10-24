from __future__ import annotations

from app import docs, matching, standards

SKILL_NAMES = (
    "vacature-analyse",
    "cv-brief-standaarden",
    "vacaturebron-recon",
    "career-gui-review",
)


def _job():
    return {
        "id": 1,
        "title": "Medewerker Groen",
        "company": "Gemeente Amsterdam",
        "description": "Buiten werken in Amsterdam, 24 uur per week, dagdienst.",
        "source_id": "test",
        "url": "https://example.org/job",
        "location": "Amsterdam",
        "lat": None,
        "lon": None,
    }


def test_project_skills_exist_and_strip_frontmatter():
    for name in SKILL_NAMES:
        body = standards.load_skill(name)
        assert len(body) > 400, f"{name} is missing or suspiciously short"
        assert not body.startswith("---"), f"{name} still has frontmatter"
        assert body.startswith("#"), f"{name} should start with a heading"


def test_cv_and_letter_prompts_carry_the_standards():
    for prompt in (
        docs.cv_prompt(_job(), "profieltekst"),
        docs.letter_prompt(_job(), "profieltekst"),
    ):
        assert "Standaarden (verplicht volgen)" in prompt
        assert "cv-brief-standaarden" in prompt
        assert "vacature-analyse" in prompt
        assert "1 pagina A4" in prompt
        assert "maximaal 15" in prompt.lower()


def test_matcher_prompt_carries_vacancy_analysis():
    prompt = matching.verdict_prompt(_job(), {"tracks": []}, "profieltekst")
    assert "Vacature-analyse (verplichte velden)" in prompt
    assert "Rode vlaggen" in prompt


def test_missing_skills_degrade_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(standards, "SKILLS_DIR", tmp_path / "missing")
    standards._read_cached.cache_clear()
    assert standards.load_skill("cv-brief-standaarden") == ""
    assert standards.standards_block(("cv-brief-standaarden",)) == ""
    prompt = docs.cv_prompt(_job(), "profieltekst")
    assert "Standaarden" not in prompt
    assert "Geef alleen JSON" in prompt


def test_all_accounts_receive_the_same_neutral_standards():
    from app import tenancy

    with tenancy.scope({"id": "legacy", "username": "owner"}):
        first = standards.standards_block(("vacature-analyse",))
    with tenancy.scope({"id": "other", "username": "other"}):
        assert standards.standards_block(("vacature-analyse",)) == first
    assert "expliciete voorkeuren" in first
