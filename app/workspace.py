"""Owner-scoped filesystem layout, resolved at call time."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import config, tenancy


@dataclass(frozen=True)
class Workspace:
    owner: str
    root: Path
    profile_dir: Path
    jobs_dir: Path
    templates_dir: Path
    seed_dir: Path

    @property
    def cv_template(self) -> Path:
        return self.templates_dir / "cv-template.docx"

    @property
    def identity_file(self) -> Path:
        return self.templates_dir / "identity.json"


def for_owner(owner: str) -> Workspace:
    data = Path(config.DATA_DIR)
    if owner == tenancy.LEGACY_ID:
        return Workspace(
            owner=owner,
            root=data,
            profile_dir=data / "profile",
            jobs_dir=data / "jobs",
            templates_dir=data / "templates",
            seed_dir=Path(config.SEED_DIR),
        )
    root = data / "users" / owner
    return Workspace(
        owner=owner,
        root=root,
        profile_dir=root / "profile",
        jobs_dir=root / "jobs",
        templates_dir=root / "templates",
        seed_dir=root / "seed",
    )


def current() -> Workspace:
    return for_owner(tenancy.user_id())
