from __future__ import annotations

import json

from app import config, profile, store


def test_draft_accept_writes_canon_and_cursor(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    profile_dir = data_dir / "profile"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "app.db")
    monkeypatch.setattr(config, "PROFILE_DIR", profile_dir)
    monkeypatch.setattr(profile, "PROFILE_FILE", profile_dir / "profile.md")
    monkeypatch.setattr(profile, "PREFERENCES_FILE", profile_dir / "preferences.json")
    monkeypatch.setattr(profile, "ANSWER_LOG_FILE", profile_dir / "answer-log.md")
    monkeypatch.setattr(profile, "INTERVIEW_PLAN_FILE", profile_dir / "interview-plan.md")
    monkeypatch.setattr(profile, "HISTORY_DIR", profile_dir / "history")
    monkeypatch.setattr(profile, "DRAFTS_DIR", profile_dir / "drafts")
    store.init_db()

    preferences = profile.Preferences().model_dump()
    path = profile.save_draft("# Nieuw profiel\n\n## Kern\nKern.", preferences, {"mode": "test"})
    pair = profile.pending_draft()
    assert pair is not None and pair[0] == path

    profile.accept_draft(path)
    assert (profile_dir / "profile.md").read_text().startswith("# Nieuw profiel")
    written = json.loads((profile_dir / "preferences.json").read_text())
    assert written["tracks"] == []
    assert profile.pending_draft() is None
    assert store.meta_get("bootstrap_status") == "done"
    assert store.meta_get("answer_log_offset") == "0"

    second = profile.save_draft("# Tweede", preferences, {})
    profile.discard_draft(second)
    assert profile.pending_draft() is None
    assert store.meta_get("bootstrap_status") == "idle"
    assert (profile_dir / "profile.md").read_text().startswith("# Nieuw profiel")
