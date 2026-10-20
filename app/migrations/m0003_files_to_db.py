"""Import existing JSON/markdown state into the Phase 3 tables, without deleting files."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import yaml

from .. import config
from ..util import now_iso

ANSWER_RE = re.compile(
    r"^##\s+(?P<stamp>[^\n]+)\n+\*\*Vraag:\*\*\s*(?P<question>.*?)\n+"
    r"\*\*Antwoord:\*\*\s*(?P<answer>.*?)(?=\n##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _owners(data_dir: Path) -> list[tuple[str, Path]]:
    owners = [("legacy", data_dir / "profile")]
    users = data_dir / "users"
    if users.exists():
        for path in sorted(users.iterdir()):
            if path.is_dir():
                owners.append((path.name, path / "profile"))
    return owners


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _insert(con, owner: str, profile_dir: Path) -> None:
    profile_path = profile_dir / "profile.md"
    if profile_path.exists():
        con.execute(
            "INSERT OR IGNORE INTO profiles(owner, profile_md, updated_at) VALUES (?,?,?)",
            (owner, profile_path.read_text(), now_iso()),
        )
    preferences_path = profile_dir / "preferences.json"
    if preferences_path.exists():
        data = _read_json(preferences_path)
        if isinstance(data, dict):
            con.execute(
                "INSERT OR IGNORE INTO preferences(owner, data_json, updated_at) VALUES (?,?,?)",
                (owner, json.dumps(data, ensure_ascii=False), now_iso()),
            )
    feedback_path = profile_dir / "feedback.json"
    if feedback_path.exists():
        data = _read_json(feedback_path)
        if isinstance(data, dict):
            con.execute(
                "INSERT OR IGNORE INTO feedback_profiles(owner, data_json, updated_at) "
                "VALUES (?,?,?)",
                (owner, json.dumps(data, ensure_ascii=False), now_iso()),
            )
    state_path = profile_dir / "interview-state.json"
    if state_path.exists():
        data = _read_json(state_path)
        if isinstance(data, dict):
            con.execute(
                "INSERT OR IGNORE INTO interview_state(owner, data_json, updated_at) "
                "VALUES (?,?,?)",
                (owner, json.dumps(data, ensure_ascii=False), now_iso()),
            )
    answer_path = profile_dir / "answer-log.md"
    if answer_path.exists():
        existing = con.execute("SELECT COUNT(*) FROM answers WHERE owner=?", (owner,)).fetchone()[0]
        if existing == 0:
            for match in ANSWER_RE.finditer(answer_path.read_text()):
                con.execute(
                    "INSERT INTO answers(owner, session_id, question, answer, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (
                        owner,
                        None,
                        match.group("question").strip(),
                        match.group("answer").strip(),
                        match.group("stamp").strip(),
                    ),
                )
    drafts_dir = profile_dir / "drafts"
    if drafts_dir.exists():
        for path in sorted(drafts_dir.glob("consolidation-*.json")):
            data = _read_json(path)
            if not isinstance(data, dict) or "profile_md" not in data:
                continue
            con.execute(
                "INSERT INTO profile_drafts(owner, status, profile_md, preferences_json, "
                "meta_json, created_at, decided_at) VALUES (?,?,?,?,?,?,?)",
                (
                    owner,
                    str(data.get("status") or "pending"),
                    str(data["profile_md"]),
                    json.dumps(data.get("preferences") or {}, ensure_ascii=False),
                    json.dumps(
                        {
                            key: value
                            for key, value in data.items()
                            if key not in {"status", "profile_md", "preferences"}
                        },
                        ensure_ascii=False,
                    ),
                    str(data.get("created_at") or now_iso()),
                    data.get("accepted_at") or data.get("discarded_at"),
                ),
            )


def _insert_sources(con, data_dir: Path) -> None:
    path = data_dir / "sources.yaml"
    if not path.exists():
        return
    try:
        payload = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return
    for source in payload.get("sources") or []:
        source_id = source.get("id")
        if not source_id:
            continue
        con.execute(
            "INSERT OR IGNORE INTO source_configs(id, config_json, enabled, updated_at) "
            "VALUES (?,?,?,?)",
            (
                source_id,
                json.dumps(source, ensure_ascii=False),
                1 if source.get("enabled", True) else 0,
                now_iso(),
            ),
        )


def up(con: sqlite3.Connection) -> None:
    data_dir = Path(config.DATA_DIR)
    for owner, profile_dir in _owners(data_dir):
        _insert(con, owner, profile_dir)
    _insert_sources(con, data_dir)
