"""Local authentication: constants."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter

log = logging.getLogger("jobcraft.auth")


COOKIE_NAME = "dca_session"


CSRF_FIELD = "_csrf"


CSRF_HEADER = "x-csrf-token"


SESSION_TTL_SECONDS = 14 * 24 * 3600


ANONYMOUS_TTL_SECONDS = 3600


LAST_SEEN_REFRESH_SECONDS = 60


ROLE_USER = "user"


ROLE_ADMIN = "admin"


ROLES = (ROLE_USER, ROLE_ADMIN)


LEGACY_USER_ID = "legacy"


LEGACY_DISPLAY_NAME = "Owner"


DEFAULT_USERNAME = "owner"


DEFAULT_PASSWORD = "dev-local"


BOOTSTRAP_FILE = "bootstrap-accounts.json"


MIN_PASSWORD_LENGTH = 10


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


PUBLIC_PATHS = frozenset({"/login", "/healthz"})


BOOTSTRAP_ACCOUNTS = (
    ("admin", ROLE_ADMIN, "Beheerder"),
    ("test-user", ROLE_USER, "Testgebruiker"),
)


_USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}", re.IGNORECASE)


AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
"""


router = APIRouter()
