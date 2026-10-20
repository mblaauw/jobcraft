from __future__ import annotations

from app import config, feedback, interview_state, profile, sources, store


def test_migrations_are_versioned_and_idempotent():
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"profiles", "preferences", "answers", "profile_drafts"} <= tables
    store.init_db()
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3


def test_existing_files_are_imported_without_deletion():
    profile_dir = config.DATA_DIR / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.md").write_text("## Werk\nSynthetic profile")
    (profile_dir / "preferences.json").write_text('{"notes": "fixture"}')
    (profile_dir / "feedback.json").write_text('{"counts": {"up": 1, "down": 0}}')
    (profile_dir / "interview-state.json").write_text('{"topics": []}')
    (profile_dir / "answer-log.md").write_text(
        "## 2026-10-01T10:00:00\n**Vraag:** Wat zoek je?\n**Antwoord:** Buiten werk.\n"
    )
    drafts = profile_dir / "drafts"
    drafts.mkdir()
    (drafts / "consolidation-20261001.json").write_text(
        '{"status":"pending","profile_md":"## Werk\\nDraft",'
        '"preferences":{},"created_at":"2026-10-01T10:00:00+00:00"}'
    )
    (config.DATA_DIR / "sources.yaml").write_text("sources:\n  - id: fixture\n    enabled: true\n")
    with store.connect() as con:
        for table in (
            "profiles",
            "preferences",
            "feedback_profiles",
            "interview_state",
            "answers",
            "profile_drafts",
            "source_configs",
        ):
            con.execute(f"DELETE FROM {table}")
        con.execute("PRAGMA user_version=2")
    store.init_db()
    with store.connect() as con:
        assert (
            con.execute("SELECT profile_md FROM profiles")
            .fetchone()[0]
            .endswith("Synthetic profile")
        )
        assert con.execute("SELECT answer FROM answers").fetchone()[0] == "Buiten werk."
        assert con.execute("SELECT COUNT(*) FROM profile_drafts").fetchone()[0] == 1
        assert con.execute("SELECT id FROM source_configs").fetchone()[0] == "fixture"
    assert (profile_dir / "profile.md").exists()


def test_runtime_state_reads_from_sqlite_not_removed_files():
    profile.write_profile("## Werk\nDatabase profile", "fixture")
    profile.write_preferences({"notes": "database preferences"})
    profile.append_answer("Vraag?", "Antwoord.")
    interview_state.ensure_state()
    feedback.rebuild()
    config.user_path(profile.PROFILE_FILE).unlink()
    config.user_path(profile.PREFERENCES_FILE).unlink()
    assert profile.read_profile().strip().endswith("Database profile")
    assert profile.read_preferences()["notes"] == "database preferences"
    assert store.list_answers()[0]["answer"] == "Antwoord."
    assert interview_state.summary()["topics"]
    assert feedback.load()["counts"] == {"up": 0, "down": 0}


def test_source_configuration_is_persisted_in_sqlite():
    sources.ensure_sources_file()
    entries = sources.load_sources()
    assert entries and store.load_source_configs()
    source_id = entries[0]["id"]
    assert sources.set_enabled(source_id, False)
    stored = {entry["id"]: entry for entry in store.load_source_configs()}
    assert stored[source_id]["enabled"] is False


def test_existing_database_is_backed_up_before_migrating():
    job_id, _ = store.upsert_job(
        {
            "source_id": "fixture",
            "url": "https://example.test/migration",
            "title": "Fixture",
            "description": "Synthetic",
            "content_hash": "migration",
        }
    )
    with store.connect() as con:
        con.execute("DROP TABLE profiles")
        con.execute("PRAGMA user_version=0")
    store.init_db()
    backups = list((config.DATA_DIR / "backups").glob("app-*.db"))
    assert backups
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        assert con.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
