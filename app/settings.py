"""Runtime settings resolved once per process, injectable in tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    data_dir: Path
    runtime_dir: Path
    seed_dir: Path
    prompts_dir: Path
    templates_dir: Path
    static_dir: Path
    skills_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        base_dir=BASE_DIR,
        data_dir=_path("APP_DATA_DIR", BASE_DIR / "data"),
        runtime_dir=_path("APP_RUNTIME_DIR", BASE_DIR / ".runtime"),
        seed_dir=_path("APP_SEED_DIR", BASE_DIR / "seed"),
        prompts_dir=BASE_DIR / "prompts",
        templates_dir=BASE_DIR / "app" / "templates",
        static_dir=BASE_DIR / "app" / "static",
        skills_dir=BASE_DIR / ".opencode" / "skills",
    )


def reset_settings() -> None:
    get_settings.cache_clear()
