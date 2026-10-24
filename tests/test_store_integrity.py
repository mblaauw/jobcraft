import sqlite3

import pytest

from app import store
from app.migrations import m0006_foreign_keys as migration


def test_connection_context_closes_commits_and_rolls_back():
    with store.connect() as con:
        con.execute("CREATE TABLE connection_probe(value TEXT)")
        con.execute("INSERT INTO connection_probe VALUES ('committed')")
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1")
    with pytest.raises(RuntimeError), store.connect() as failed:
        failed.execute("INSERT INTO connection_probe VALUES ('rolled back')")
        raise RuntimeError("synthetic")
    with pytest.raises(sqlite3.ProgrammingError):
        failed.execute("SELECT 1")
    with store.connect() as check:
        assert [row[0] for row in check.execute("SELECT value FROM connection_probe")] == [
            "committed"
        ]


def test_dynamic_columns_reject_unknown_names():
    with pytest.raises(ValueError, match="column"):
        store.update_session("no-session", **{"title=?,owner": "attack"})
    with store.connect() as con, pytest.raises(ValueError, match="column"):
        store._set_job_state(con, 1, **{"status) VALUES (1); --": "attack"})


def legacy_database():
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
        CREATE TABLE jobs(id INTEGER PRIMARY KEY);
        CREATE TABLE sessions(id TEXT PRIMARY KEY,owner TEXT NOT NULL);
        CREATE TABLE job_states(owner TEXT NOT NULL,job_id INTEGER NOT NULL,PRIMARY KEY(owner,job_id));
        CREATE TABLE feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER NOT NULL);
        CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,owner TEXT,kind TEXT,version INTEGER,UNIQUE(owner,job_id,kind,version));
        CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,owner TEXT NOT NULL);
        CREATE INDEX messages_owner ON messages(owner);
        INSERT INTO jobs VALUES (1);
        INSERT INTO sessions VALUES ('session','a');
        INSERT INTO job_states VALUES ('a',1);
        INSERT INTO feedback VALUES (1,1);
        INSERT INTO documents VALUES (1,1,'a','resume',1);
        INSERT INTO messages VALUES (1,'session','a');
        INSERT INTO messages VALUES (99,'session','a');
        DELETE FROM messages WHERE id=99;
        PRAGMA user_version=5;
    """)
    return con


def test_migration_enforces_relationships_preserves_ids_indexes_and_sequence():
    con = legacy_database()
    try:
        store.migrate(con)
        assert con.execute("PRAGMA user_version").fetchone()[0] == 6
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
        assert con.execute("SELECT id FROM documents").fetchone()[0] == 1
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='messages_owner'").fetchone()
        cursor = con.execute("INSERT INTO messages(session_id,owner) VALUES ('session','a')")
        assert cursor.lastrowid == 100
        for sql in [
            "INSERT INTO job_states VALUES ('a',999)",
            "INSERT INTO feedback(job_id) VALUES (999)",
            "INSERT INTO documents(job_id) VALUES (999)",
            "INSERT INTO messages(session_id,owner) VALUES ('session','other')",
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(sql)
        con.rollback()
        store.migrate(con)
        assert con.execute("PRAGMA user_version").fetchone()[0] == 6
    finally:
        con.close()


@pytest.mark.parametrize(
    "bad_sql",
    [
        "INSERT INTO feedback(job_id) VALUES (999)",
        "INSERT INTO messages(session_id,owner) VALUES ('session','other')",
    ],
)
def test_migration_refuses_orphans_without_mutating(bad_sql):
    con = legacy_database()
    try:
        con.execute(bad_sql)
        con.commit()
        before = list(con.iterdump())
        with pytest.raises(RuntimeError, match="preflight"):
            store.migrate(con)
        assert list(con.iterdump()) == before
        assert con.execute("PRAGMA user_version").fetchone()[0] == 5
    finally:
        con.close()


def test_migration_failure_after_first_rebuild_rolls_back(monkeypatch):
    con = legacy_database()
    original = migration._rebuild
    calls = []

    def fail_second(connection, table, constraint):
        calls.append(table)
        if len(calls) == 2:
            raise RuntimeError("synthetic rebuild failure")
        original(connection, table, constraint)

    try:
        before = list(con.iterdump())
        monkeypatch.setattr(migration, "_rebuild", fail_second)
        with pytest.raises(RuntimeError, match="synthetic rebuild"):
            store.migrate(con)
        assert list(con.iterdump()) == before
        assert con.execute("PRAGMA user_version").fetchone()[0] == 5
    finally:
        con.close()
