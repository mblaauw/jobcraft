from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from app import config, feedback, interview_state, profile, sources, store
from app.store import schema


def test_migrations_are_versioned_and_idempotent():
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 5
        tables = {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"profiles", "preferences", "answers", "profile_drafts"} <= tables
    store.init_db()
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 5


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
    assert "**Antwoord:** Antwoord." in profile.read_answer_log()
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
        assert con.execute("PRAGMA user_version").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        assert con.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()


def test_versioned_upgrade_backs_up_profiles_even_without_jobs():
    profile.write_profile("## Werk\nSynthetic retained profile", "fixture")
    with store.connect() as con:
        con.execute("PRAGMA user_version=4")
    store.init_db()
    backups = list((config.DATA_DIR / "backups").glob("app-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 4
        assert (
            "Synthetic retained profile"
            in con.execute("SELECT profile_md FROM profiles").fetchone()[0]
        )
    store.init_db()
    assert list((config.DATA_DIR / "backups").glob("app-*.db")) == backups


def test_backup_precedes_legacy_schema_and_data_changes():
    with store.connect() as con:
        con.execute("PRAGMA user_version=4")
        con.execute("DROP TABLE feedback")
        con.execute("DELETE FROM meta WHERE key='search_text_v1'")
    store.init_db()
    backup = next((config.DATA_DIR / "backups").glob("app-*.db"))
    with sqlite3.connect(backup) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 4
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='feedback'").fetchone() is None
        assert con.execute("SELECT 1 FROM meta WHERE key='search_text_v1'").fetchone() is None


def test_unversioned_data_fix_also_requires_backup():
    with store.connect() as con:
        con.execute("DELETE FROM meta WHERE key='posted_at_normalized_v2'")
    store.init_db()
    assert len(list((config.DATA_DIR / "backups").glob("app-*.db"))) == 1


def test_backup_failure_aborts_before_mutating_database(monkeypatch):
    with store.connect() as con:
        con.execute("PRAGMA user_version=4")
        con.execute("DROP TABLE feedback")

    def fail(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(schema, "_backup_database", fail)
    with pytest.raises(OSError, match="synthetic disk failure"):
        store.init_db()
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 4
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='feedback'").fetchone() is None


def test_repeated_backup_never_overwrites_and_retains_five():
    paths = []
    for index in range(7):
        with store.connect() as con:
            con.execute("PRAGMA user_version=4")
        store.init_db()
        current = set((config.DATA_DIR / "backups").glob("app-*.db"))
        new = current - set(paths)
        assert len(new) == 1
        paths.extend(new)
        assert current == set(paths[-5:]), f"wrong retention after backup {index}"
    backups = list((config.DATA_DIR / "backups").glob("app-*.db"))
    assert len(backups) == 5
    for backup in backups:
        with sqlite3.connect(backup) as con:
            assert con.execute("PRAGMA user_version").fetchone()[0] == 4


def test_backup_includes_committed_uncheckpointed_wal():
    with closing(store.connect()) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA user_version=4")
        writer.execute(
            "INSERT INTO profiles(owner,profile_md,updated_at) VALUES (?,?,?)",
            ("fixture", "Committed WAL profile", store.now()),
        )
        writer.commit()
        assert config.DB_PATH.with_name("app.db-wal").stat().st_size > 0
        store.init_db()
        backup = next((config.DATA_DIR / "backups").glob("app-*.db"))
        with closing(sqlite3.connect(backup)) as con:
            assert (
                con.execute("SELECT profile_md FROM profiles WHERE owner='fixture'").fetchone()[0]
                == "Committed WAL profile"
            )


def test_partial_backup_is_removed_on_failure():
    class BrokenSource:
        def backup(self, destination):
            raise OSError("synthetic backup failure")

    with pytest.raises(OSError, match="synthetic backup failure"):
        store._backup_database(BrokenSource(), 4)
    assert list((config.DATA_DIR / "backups").glob("app-*.db")) == []
