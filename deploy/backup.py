#!/usr/bin/env python3
"""Back up Jobcraft's data folder on the host: the database plus every account's files.

    python3 deploy/backup.py --data data --dest ../jobcraft-backups --label daily --keep 14
    python3 deploy/backup.py --verify ../jobcraft-backups/jobcraft-daily-<stamp>.tgz

Standard library only, so it runs on the host without the app's environment. The
database is copied with SQLite's online backup, which is safe while the app writes,
and the copy must pass an integrity check before it is kept. Caches and the meaning
index are left out: the app rebuilds them. Secrets (.env.local) are never included.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

# What a restore needs besides the database; everything else under data/ is rebuilt.
KEEP = ("users", "profile", "vacancy-readings", "sources.yaml")
COUNTED = ("users", "jobs", "job_states", "user_meta", "documents")


def _copy_database(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source, timeout=30)) as live, closing(sqlite3.connect(target)) as copy:
        live.backup(copy)
        result = copy.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"de kopie van de database is niet in orde: {result}")


def backup(data: Path, dest: Path, label: str, keep: int) -> Path:
    database = data / "app.db"
    if not database.exists():
        raise FileNotFoundError(f"geen database in {data}")
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    final = dest / f"jobcraft-{label}-{stamp}.tgz"
    with tempfile.TemporaryDirectory(dir=dest, prefix=".backup-") as work:
        copy = Path(work) / "app.db"
        _copy_database(database, copy)
        partial = Path(work) / final.name
        with tarfile.open(partial, "w:gz") as archive:
            archive.add(copy, arcname="app.db")
            for name in KEEP:
                if (data / name).exists():
                    archive.add(data / name, arcname=name)
        os.chmod(partial, 0o600)
        os.replace(partial, final)
    for old in sorted(dest.glob(f"jobcraft-{label}-*.tgz"), reverse=True)[keep:]:
        old.unlink()
    return final


def verify(path: Path) -> dict:
    """Open the archive's database the way a restore would and count what is in it."""
    with tempfile.TemporaryDirectory(prefix="jobcraft-verify-") as work:
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
            archive.extract("app.db", work, filter="data")
        with closing(sqlite3.connect(f"file:{Path(work) / 'app.db'}?mode=ro", uri=True)) as con:
            counts = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNTED}
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    return {"integrity": integrity, "files": len(names), **counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--dest", type=Path, default=Path("../jobcraft-backups"))
    parser.add_argument("--label", default="daily")
    parser.add_argument("--keep", type=int, default=14)
    parser.add_argument("--verify", type=Path, help="check an existing backup instead of making one")
    args = parser.parse_args(argv)
    if args.verify:
        print(verify(args.verify))
        return 0
    if not args.label.isalnum() or args.keep < 1:
        parser.error("--label must be letters/digits and --keep at least 1")
    try:
        made = backup(args.data, args.dest, args.label, args.keep)
    except Exception as error:  # cron mails/logs this line; a half backup is never left behind
        print(f"{datetime.now(UTC).isoformat()} backup mislukt: {error}", file=sys.stderr)
        return 1
    print(f"{datetime.now(UTC).isoformat()} backup ok: {made.name} ({made.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
