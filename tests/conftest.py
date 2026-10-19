"""Shared isolation: no test may touch real data, seed material or the network."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_ROOT = Path(tempfile.mkdtemp(prefix="jobcraft-tests-"))
DATA_ROOT = SESSION_ROOT / "data"
RUNTIME_ROOT = SESSION_ROOT / "runtime"
SEED_ROOT = SESSION_ROOT / "seed"

# These must be set before any test module imports app.config.
os.environ["APP_DATA_DIR"] = str(DATA_ROOT)
os.environ["APP_RUNTIME_DIR"] = str(RUNTIME_ROOT)
os.environ["APP_SEED_DIR"] = str(SEED_ROOT)
os.environ["APP_SKIP_LLM"] = "1"
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_BASE_URL"] = ""
os.environ["RUNWARE_API_KEY"] = ""

_REAL_DB = REPO_ROOT / "data" / "app.db"
_REAL_DB_STATE = (
    (_REAL_DB.stat().st_size, _REAL_DB.stat().st_mtime_ns) if _REAL_DB.exists() else None
)
_REAL_DATA_EXISTED = (REPO_ROOT / "data").exists()
_REAL_RUNTIME_EXISTED = (REPO_ROOT / ".runtime").exists()
_REAL_SEED_EXISTED = (REPO_ROOT / "seed").exists()

from app import config, docs, feedback, interview_state, profile, sources, store  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Point every module-level path at a disposable per-test instance."""
    root = tmp_path / "instance"
    data = root / "data"
    profile_dir = data / "profile"
    jobs_dir = data / "jobs"
    runtime = root / "runtime"
    seed = root / "seed"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "app.db")
    monkeypatch.setattr(config, "PROFILE_DIR", profile_dir)
    monkeypatch.setattr(config, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(config, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(config, "SEED_DIR", seed)
    monkeypatch.setattr(profile, "PROFILE_FILE", profile_dir / "profile.md")
    monkeypatch.setattr(profile, "PREFERENCES_FILE", profile_dir / "preferences.json")
    monkeypatch.setattr(profile, "ANSWER_LOG_FILE", profile_dir / "answer-log.md")
    monkeypatch.setattr(profile, "INTERVIEW_PLAN_FILE", profile_dir / "interview-plan.md")
    monkeypatch.setattr(profile, "HISTORY_DIR", profile_dir / "history")
    monkeypatch.setattr(profile, "DRAFTS_DIR", profile_dir / "drafts")
    monkeypatch.setattr(interview_state, "STATE_FILE", profile_dir / "interview-state.json")
    monkeypatch.setattr(interview_state, "PLAN_FILE", profile_dir / "interview-plan.md")
    monkeypatch.setattr(interview_state, "ANSWER_LOG_FILE", profile_dir / "answer-log.md")
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", profile_dir / "feedback.json")
    monkeypatch.setattr(docs, "DOC_TEMPLATES_DIR", data / "templates")
    monkeypatch.setattr(docs, "CV_TEMPLATE", data / "templates" / "cv-template.docx")
    monkeypatch.setattr(docs, "IDENTITY_FILE", data / "templates" / "identity.json")
    monkeypatch.setattr(sources, "DATA_FILE", data / "sources.yaml")
    store.init_db()
    return root


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Fail fast if an adapter attempts a real socket; MockTransport still works."""
    import httpx

    def blocked(*args, **kwargs):
        raise RuntimeError("network disabled in tests")

    async def blocked_async(*args, **kwargs):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)


@pytest.fixture(scope="session", autouse=True)
def guard_real_workspace():
    yield
    if not _REAL_DATA_EXISTED:
        assert not (REPO_ROOT / "data").exists(), "tests created data/"
    if not _REAL_RUNTIME_EXISTED:
        assert not (REPO_ROOT / ".runtime").exists(), "tests created .runtime/"
    if not _REAL_SEED_EXISTED:
        assert not (REPO_ROOT / "seed").exists(), "tests created seed/"
    if _REAL_DB_STATE is not None and _REAL_DB.exists():
        current = (_REAL_DB.stat().st_size, _REAL_DB.stat().st_mtime_ns)
        assert current == _REAL_DB_STATE, "tests modified the real data/app.db"
    shutil.rmtree(SESSION_ROOT, ignore_errors=True)
