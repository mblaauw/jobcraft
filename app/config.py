"""Central settings for the Jobcraft."""

from __future__ import annotations

import os
from pathlib import Path

from . import tenancy

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


DATA_DIR = _env_path("APP_DATA_DIR", BASE_DIR / "data")
RUNTIME_DIR = _env_path("APP_RUNTIME_DIR", BASE_DIR / ".runtime")
SEED_DIR = _env_path("APP_SEED_DIR", BASE_DIR / "seed")

PROFILE_DIR = DATA_DIR / "profile"
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "app.db"

RUNTIME_HOME = RUNTIME_DIR / "opencode"
RUNTIME_CONFIG = RUNTIME_HOME / ".config/opencode"
RUNTIME_AGENTS = RUNTIME_CONFIG / "agents"
RUNTIME_WORKSPACE = RUNTIME_DIR / "workspace"
RUNTIME_LOG = RUNTIME_DIR / "opencode-server.log"

AGENTS_SOURCE = BASE_DIR / "agents"
PROMPTS_DIR = BASE_DIR / "prompts"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"

OPENCODE_BIN = os.environ.get("OPENCODE_BIN", "opencode")
OPENCODE_HOST = os.environ.get("OPENCODE_HOST", "127.0.0.1")
OPENCODE_PORT = int(os.environ.get("OPENCODE_PORT", "4190"))
PROVIDER_ID = os.environ.get("OPENCODE_PROVIDER", "opencode-go")
MODEL_ID = os.environ.get("OPENCODE_MODEL", "deepseek-v4.1-flash")
OPENCODE_GO_KEY = os.environ.get("OPENCODE_GO_KEY", "")
OPENCODE_GO_KEY_FILE = os.environ.get("OPENCODE_GO_KEY_FILE", "")

APP_USER = os.environ.get("APP_USER", "owner")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
APP_SKIP_LLM = os.environ.get("APP_SKIP_LLM", "0") == "1"

AGENT_INTERVIEWER = "interviewer"
AGENT_WRITER = "writer"

KICKOFF_TEXT = "Start het gesprek met één concrete vraag."

SEED_FILES = {
    "dossier": "dossier.md",
    "profile_sketch": "profile-sketch.md",
    "strategy": "strategy.md",
    "cv": "cv.docx",
    "photo": "photo.jpg",
}

MODEL_LABEL = f"{PROVIDER_ID}/{MODEL_ID}"


def workspace_dir() -> Path:
    return (
        DATA_DIR
        if tenancy.user_id() == tenancy.LEGACY_ID
        else DATA_DIR / "users" / tenancy.user_id()
    )


def profile_dir() -> Path:
    return PROFILE_DIR if tenancy.user_id() == tenancy.LEGACY_ID else workspace_dir() / "profile"


def jobs_dir() -> Path:
    return JOBS_DIR if tenancy.user_id() == tenancy.LEGACY_ID else workspace_dir() / "jobs"


def seed_dir() -> Path:
    return SEED_DIR if tenancy.user_id() == tenancy.LEGACY_ID else workspace_dir() / "seed"


def seed_files() -> dict:
    return SEED_FILES if tenancy.user_id() == tenancy.LEGACY_ID else {"cv": "cv.docx"}


def user_path(legacy_path: Path, section: str = "profile") -> Path:
    """Keep legacy artifacts in place; resolve new-user artifacts per operation."""
    return (
        legacy_path
        if tenancy.user_id() == tenancy.LEGACY_ID
        else workspace_dir() / section / legacy_path.name
    )
