"""Owner/session-bound CV inspection; canonical publication only after confirmation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

from . import config, docs, tenancy

PREVIEW_SECONDS = 15 * 60
UNAVAILABLE = "Deze controle is verlopen of niet beschikbaar. Kies je DOCX-bestand opnieuw."


def _directory() -> Path:
    return config.user_path(docs.DOC_TEMPLATES_DIR, "templates") / ".cv-previews"


def _remove(token: str) -> None:
    for suffix in (".json", ".docx"):
        (_directory() / (token + suffix)).unlink(missing_ok=True)


def _load(token: str, session: str) -> tuple[dict, bytes]:
    if not session or not re.fullmatch(r"[a-f0-9]{48}", token):
        raise ValueError(UNAVAILABLE)
    try:
        meta = json.loads((_directory() / (token + ".json")).read_text())
        raw = (_directory() / (token + ".docx")).read_bytes()
    except (OSError, ValueError):
        raise ValueError(UNAVAILABLE) from None
    if (
        meta.get("owner") != tenancy.user_id()
        or meta.get("session") != session
        or meta.get("expires_at", 0) <= time.time()
        or meta.get("source_hash") != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError(UNAVAILABLE)
    return meta, raw


def stage(raw: bytes, filename: str, session: str) -> dict:
    if not session:
        raise ValueError(UNAVAILABLE)
    inspection = docs.inspect_cv(raw)
    docs.prepare_template(raw)  # all preparation must succeed before offering confirmation
    token = secrets.token_hex(24)
    meta = {
        "owner": tenancy.user_id(),
        "session": session,
        "expires_at": time.time() + PREVIEW_SECONDS,
        "source_hash": inspection["source_hash"],
        "filename": Path(filename).name[:200],
        "inspection": {
            key: inspection[key] for key in ("profile_heading", "strong_heading", "strong_count")
        },
    }
    with docs.template_lock():
        directory = _directory()
        directory.mkdir(parents=True, exist_ok=True)
        for entry in directory.glob("*.json"):
            try:
                expired = json.loads(entry.read_text()).get("expires_at", 0) <= time.time()
            except (OSError, ValueError):
                expired = True
            if expired and re.fullmatch(r"[a-f0-9]{48}", entry.stem):
                _remove(entry.stem)
        existing = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        for entry in existing[:-9]:
            if re.fullmatch(r"[a-f0-9]{48}", entry.stem):
                _remove(entry.stem)
        try:
            with (directory / (token + ".docx")).open("xb") as output:
                output.write(raw)
            with (directory / (token + ".json")).open("x") as output:
                json.dump(meta, output, ensure_ascii=False)
        except Exception:
            _remove(token)
            raise
    return {"token": token, "filename": meta["filename"], "inspection": meta["inspection"]}


def _publish_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(raw)
            output.close()
            os.link(temporary, path)  # atomic and refuses to overwrite any existing artifact
        finally:
            temporary.unlink(missing_ok=True)


def confirm(token: str, session: str) -> dict:
    with docs.template_lock():
        meta, raw = _load(token, session)
        template, identity = docs.prepare_template(raw)
        source = config.seed_dir() / config.seed_files()["cv"]
        target = config.user_path(docs.CV_TEMPLATE, "templates")
        identity_path = config.user_path(docs.IDENTITY_FILE, "templates")
        if any(path.exists() for path in (source, target, identity_path)):
            raise FileExistsError(
                "Je basis-CV is al toegevoegd; bestaande documenten blijven behouden."
            )
        created = []
        try:
            for path, content in (
                (source, raw),
                (target, template),
                (identity_path, json.dumps(identity, ensure_ascii=False, indent=2).encode()),
            ):
                _publish_exclusive(path, content)
                created.append(path)
        except Exception:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise
        _remove(token)
        return meta["inspection"]


def discard(token: str, session: str) -> None:
    with docs.template_lock():
        _load(token, session)
        _remove(token)
