"""SQLite sources operations."""
from __future__ import annotations

import json

from .. import tenancy
from .connection import connect, now


def list_user_sources(owner: str | None = None) -> dict[str, bool]:
    with connect() as con:
        rows = con.execute(
            "SELECT source_id, enabled FROM user_sources WHERE owner=?",
            (owner or tenancy.user_id(),),
        ).fetchall()
    return {row["source_id"]: bool(row["enabled"]) for row in rows}



def set_user_source(source_id: str, enabled: bool, owner: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO user_sources(owner, source_id, enabled) VALUES (?,?,?) "
            "ON CONFLICT(owner, source_id) DO UPDATE SET enabled=excluded.enabled",
            (owner or tenancy.user_id(), source_id, 1 if enabled else 0),
        )



def load_source_configs() -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT config_json, enabled FROM source_configs ORDER BY id").fetchall()
    configs = []
    for row in rows:
        try:
            entry = json.loads(row["config_json"])
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id"):
            entry["enabled"] = bool(row["enabled"])
            configs.append(entry)
    return configs



def save_source_config(entry: dict, enabled: bool | None = None) -> None:
    source_id = entry.get("id")
    if not source_id:
        return
    state = bool(entry.get("enabled", True)) if enabled is None else enabled
    with connect() as con:
        con.execute(
            "INSERT INTO source_configs(id, config_json, enabled, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json, "
            "enabled=excluded.enabled, updated_at=excluded.updated_at",
            (source_id, json.dumps(entry, ensure_ascii=False), 1 if state else 0, now()),
        )



def set_source_config_enabled(source_id: str, enabled: bool) -> bool:
    with connect() as con:
        cursor = con.execute(
            "UPDATE source_configs SET enabled=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, now(), source_id),
        )
    return cursor.rowcount > 0



def sync_sources(entries: list[dict]) -> None:
    with connect() as con:
        for entry in entries:
            con.execute(
                "INSERT INTO sources(id, name, url, kind, config_json, enabled, notes) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, "
                "kind=excluded.kind, config_json=excluded.config_json, notes=excluded.notes, "
                "enabled=excluded.enabled",
                (
                    entry["id"],
                    entry.get("name") or entry["id"],
                    entry.get("base_url"),
                    entry.get("kind"),
                    json.dumps(entry, ensure_ascii=False),
                    1 if entry.get("enabled", True) else 0,
                    entry.get("notes"),
                ),
            )



def list_sources() -> list:
    with connect() as con:
        return con.execute("SELECT * FROM sources ORDER BY name").fetchall()



def update_source_status(source_id: str, status: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE sources SET last_scan_at=?, last_status=? WHERE id=?",
            (now(), status, source_id),
        )

