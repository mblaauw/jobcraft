"""The host backup: a checked copy of a live database plus the account files, and pruning."""

from __future__ import annotations

import importlib.util
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("backup", Path(__file__).parents[1] / "deploy" / "backup.py")
backup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backup)


def _data(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    data = tmp_path / "data"
    (data / "users" / "u1" / "jobs").mkdir(parents=True)
    (data / "users" / "u1" / "jobs" / "cv-v1.pdf").write_bytes(b"%PDF")
    (data / "runtime" / "http-cache").mkdir(parents=True)
    (data / "runtime" / "http-cache" / "page.json").write_text("{}")
    con = sqlite3.connect(data / "app.db")
    con.execute("PRAGMA journal_mode=WAL")
    for table in backup.COUNTED:
        con.execute(f"CREATE TABLE {table} (id INTEGER)")
    con.executemany("INSERT INTO jobs VALUES (?)", [(i,) for i in range(5)])
    con.commit()
    return data, con  # left open: the backup must work while the app holds the database


def test_a_backup_holds_the_live_database_and_account_files_but_no_cache(tmp_path):
    data, live = _data(tmp_path)
    with closing(live):
        live.execute("INSERT INTO jobs VALUES (99)")  # committed only to the WAL
        live.commit()
        made = backup.backup(data, tmp_path / "backups", "daily", 14)
    assert made.stat().st_mode & 0o777 == 0o600
    with tarfile.open(made) as archive:
        names = set(archive.getnames())
    assert "app.db" in names and "users/u1/jobs/cv-v1.pdf" in names
    assert not any(name.startswith("runtime") for name in names)
    assert backup.verify(made) | {"files": 0} == {"integrity": "ok", "files": 0, "users": 0, "jobs": 6, "job_states": 0, "user_meta": 0, "documents": 0}
    assert not list((tmp_path / "backups").glob(".backup-*"))  # no half work left behind


def test_only_the_newest_backups_of_a_label_are_kept(tmp_path):
    data, live = _data(tmp_path)
    live.close()
    dest = tmp_path / "backups"
    dest.mkdir()
    for stamp in ("20261101T000000Z", "20261102T000000Z", "20261103T000000Z"):
        (dest / f"jobcraft-daily-{stamp}.tgz").write_text("old")
    (dest / "jobcraft-predeploy-20261101T000000Z.tgz").write_text("other label")
    made = backup.backup(data, dest, "daily", 2)
    assert sorted(path.name for path in dest.glob("jobcraft-daily-*")) == ["jobcraft-daily-20261103T000000Z.tgz", made.name]
    assert (dest / "jobcraft-predeploy-20261101T000000Z.tgz").exists()


def test_a_failed_backup_says_so_and_keeps_nothing(tmp_path, capsys):
    assert backup.main(["--data", str(tmp_path / "missing"), "--dest", str(tmp_path / "backups")]) == 1
    assert "backup mislukt" in capsys.readouterr().err
    assert not (tmp_path / "backups").exists()
