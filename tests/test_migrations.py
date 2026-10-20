from __future__ import annotations

from app import config, store


def test_migrations_are_versioned_and_idempotent():
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"profiles", "preferences", "answers", "profile_drafts"} <= tables
    store.init_db()
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2


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
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        assert con.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
