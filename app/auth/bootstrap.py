"""Local authentication: bootstrap."""

from __future__ import annotations

import json
import os
import secrets
import threading
import uuid
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import config, store
from .constants import (
    AUTH_SCHEMA,
    BOOTSTRAP_ACCOUNTS,
    BOOTSTRAP_FILE,
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    LEGACY_DISPLAY_NAME,
    LEGACY_USER_ID,
    ROLE_USER,
    log,
)
from .timestamps import _iso
from .users import _get_user_by_id, _get_user_by_username, _insert_user

_bootstrap_credentials_lock = threading.Lock()


def bootstrap_accounts_file() -> Path:
    return Path(config.RUNTIME_DIR) / BOOTSTRAP_FILE


def _read_bootstrap_accounts() -> dict:
    path = bootstrap_accounts_file()
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    accounts = data.get("accounts") if isinstance(data, dict) else None
    return accounts if isinstance(accounts, dict) else {}


def _write_bootstrap_accounts(accounts: dict) -> None:
    """Write the one-time generated passwords atomically with mode 0600."""
    path = bootstrap_accounts_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _read_bootstrap_accounts() == accounts:
        with suppress(OSError):
            os.chmod(path, 0o600)
        return
    payload = json.dumps({"accounts": accounts}, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _ensure_schema() -> None:
    with closing(store.connect()) as con, con:
        con.executescript(AUTH_SCHEMA)
        con.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?",
            (_iso(datetime.now(UTC) - timedelta(days=7)),),
        )


def init_auth() -> None:
    """Create the auth tables and the bootstrap accounts. Idempotent.

    Existing accounts keep their password and role: rerunning never resets a
    credential. Newly generated passwords are stored once in
    ``RUNTIME_DIR/bootstrap-accounts.json`` (mode 0600) and never logged.
    """
    legacy_username = (getattr(config, "APP_USER", "") or "").strip() or DEFAULT_USERNAME
    if legacy_username.casefold() in {name.casefold() for name, *_ in BOOTSTRAP_ACCOUNTS}:
        raise RuntimeError(
            "APP_USER is reserved for a new account; keep the existing account username."
        )
    _ensure_schema()
    accounts = _read_bootstrap_accounts()
    changed = False

    legacy_username = (getattr(config, "APP_USER", "") or "").strip() or DEFAULT_USERNAME
    legacy = _get_user_by_id(LEGACY_USER_ID)
    if legacy is None:
        _insert_user(
            LEGACY_USER_ID,
            legacy_username,
            LEGACY_DISPLAY_NAME,
            ROLE_USER,
            getattr(config, "APP_PASSWORD", "") or DEFAULT_PASSWORD,
        )
        log.info("auth bootstrap: local user %r created", legacy_username)
    elif legacy["username"] != legacy_username:
        # APP_USER moved: keep the login working without touching the password or role.
        if _get_user_by_username(legacy_username) is None:
            with closing(store.connect()) as con, con:
                con.execute(
                    "UPDATE users SET username=? WHERE id=?", (legacy_username, LEGACY_USER_ID)
                )
            log.info("auth bootstrap: local username now %r", legacy_username)

    for username, role, display_name in BOOTSTRAP_ACCOUNTS:
        existing = _get_user_by_username(username)
        if existing is None:
            password = secrets.token_urlsafe(18)
            user_id = str(uuid.uuid4())
            _insert_user(user_id, username, display_name, role, password)
            accounts[username] = {"id": user_id, "role": role, "password": password}
            changed = True
            log.info("auth bootstrap: account %r created", username)
        else:
            # Idempotent: never rewrite an existing password or role.
            entry = accounts.get(username)
            if not isinstance(entry, dict) or entry.get("id") != existing["id"]:
                accounts[username] = {
                    "id": existing["id"],
                    "role": existing["role"],
                    "password": None,
                }
                changed = True

    if changed or not bootstrap_accounts_file().exists():
        _write_bootstrap_accounts(accounts)
