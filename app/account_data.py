"""Export or permanently delete everything belonging to one account."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import zipfile

from . import config, store, tenancy

# Sign-in data is never part of an export.
_NOT_EXPORTED = {"auth_sessions", "users"}
_SHARED = {"jobs", "meta", "sources", "source_configs", "token_df"}


def _owned_tables(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """Read from the live schema, so a future table cannot be silently forgotten."""
    tables = [
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not row["name"].startswith("sqlite_")
    ]
    owned = []
    for table in tables:
        columns = {row["name"] for row in con.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if "owner" in columns:
            owned.append((table, "owner"))
        elif "user_id" in columns:
            owned.append((table, "user_id"))
    return owned


def _workspace():
    root = (config.DATA_DIR / "users").resolve()
    path = config.workspace_dir().resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Dit account heeft geen eigen map.")
    return path


def export_zip() -> bytes:
    owner = tenancy.user_id()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        with store.connect() as con:
            for table, column in _owned_tables(con):
                if table in _NOT_EXPORTED:
                    continue
                rows = con.execute(f'SELECT * FROM "{table}" WHERE "{column}"=?', (owner,)).fetchall()
                archive.writestr(
                    f"gegevens/{table}.json",
                    json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2),
                )
            imported = con.execute(
                "SELECT * FROM jobs WHERE source_id=?", (store.import_source(owner),)
            ).fetchall()
            archive.writestr(
                "gegevens/zelf_toegevoegde_vacatures.json",
                json.dumps([dict(row) for row in imported], ensure_ascii=False, indent=2),
            )
        if owner != tenancy.LEGACY_ID:
            workspace = _workspace()
            if workspace.exists():
                for path in sorted(workspace.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        archive.write(path, f"bestanden/{path.relative_to(workspace)}")
    return buffer.getvalue()


def delete_account() -> None:
    """Remove rows first, then files; a failed file removal leaves nothing readable by login."""
    owner = tenancy.user_id()
    if owner == tenancy.LEGACY_ID:
        raise ValueError("Het oorspronkelijke account kan hier niet worden verwijderd.")
    workspace = _workspace()
    with store.connect() as con:
        con.execute("PRAGMA defer_foreign_keys=ON")
        for table, column in _owned_tables(con):
            if table not in _SHARED:
                con.execute(f'DELETE FROM "{table}" WHERE "{column}"=?', (owner,))
        con.execute(
            "DELETE FROM job_states WHERE job_id IN (SELECT id FROM jobs WHERE source_id=?)",
            (store.import_source(owner),),
        )
        con.execute("DELETE FROM jobs WHERE source_id=?", (store.import_source(owner),))
        con.execute("DELETE FROM users WHERE id=?", (owner,))
    if workspace.exists():
        shutil.rmtree(workspace)
