from __future__ import annotations

from app.settings import Settings, get_settings


def test_settings_expose_one_path_object():
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.db_path == settings.data_dir / "app.db"
    assert settings.prompts_dir.name == "prompts"
    assert settings.templates_dir.name == "templates"
    assert settings.static_dir.name == "static"
    assert settings.skills_dir.name == "skills"
