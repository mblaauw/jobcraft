"""Profile files: seeding, reading/writing, history and consolidation prompts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from . import config, store, tenancy
from .util import now_iso, now_local, parse_json_object

PROFILE_FILE = config.PROFILE_DIR / "profile.md"
PREFERENCES_FILE = config.PROFILE_DIR / "preferences.json"
ANSWER_LOG_FILE = config.PROFILE_DIR / "answer-log.md"
INTERVIEW_PLAN_FILE = config.PROFILE_DIR / "interview-plan.md"
HISTORY_DIR = config.PROFILE_DIR / "history"
DRAFTS_DIR = config.PROFILE_DIR / "drafts"

PRIVATE_HEADING = "Privé"
HIDDEN_HEADINGS = ("Open vragen", "Privé")
CONSOLIDATION_COST_NOTE = "~$0.005–0.018 · 20–90 s"

INTERVIEW_PLAN_DEFAULT = """# Interview plan

Topic clusters to cover over time. The interviewer updates coverage by asking;
this file is a human-readable map, not a script.

- [ ] Dagelijkse werkzaamheden die energie geven
- [ ] Werkzaamheden en omstandigheden die energie kosten
- [ ] Uren, ritme en voorspelbaarheid
- [ ] Binnen/buiten, lawaai, sociale intensiteit
- [ ] Autonomie versus begeleiding
- [ ] Fysieke belasting
- [ ] Reistijd, fiets/OV, bedrijfsauto
- [ ] Inkomen en zekerheid
- [ ] Leren, groei en ontwikkelpad
- [ ] Persoonlijke grenzen en afknappers
- [ ] Betekenis, teamcultuur en leiderschap
"""


class Track(BaseModel):
    id: str
    name: str
    weight: float = 1.0
    keywords: list[str] = Field(default_factory=list)


class PreferenceItem(BaseModel):
    tag: str
    weight: float = 1.0
    evidence: str = ""


class HardFilters(BaseModel):
    location: str = ""
    hours_min: float | None = None
    hours_max: float | None = None
    no_phone_main: bool = False
    no_structural_evenings: bool = False
    no_heavy_physical: bool = False
    day_work_only: bool = False
    wants_guidance: bool = False


class Keywords(BaseModel):
    boost: list[str] = Field(default_factory=list)
    veto: list[str] = Field(default_factory=list)


class HomeBase(BaseModel):
    """Home point for travel-time estimates (edit in preferences.json)."""

    label: str = "Nog niet ingesteld"
    lat: float = Field(default=0, ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(default=0, ge=-180, le=180, allow_inf_nan=False)
    bike_minutes: int = 30
    transit_minutes: int = 35
    car_available: bool = False


class Preferences(BaseModel):
    updated_at: str = Field(default_factory=now_iso)
    tracks: list[Track] = Field(default_factory=list)
    likes: list[PreferenceItem] = Field(default_factory=list)
    dislikes: list[PreferenceItem] = Field(default_factory=list)
    hard_filters: HardFilters = Field(default_factory=HardFilters)
    keywords: Keywords = Field(default_factory=Keywords)
    home: HomeBase = Field(default_factory=HomeBase)
    notes: str = ""

    @field_validator("tracks")
    @classmethod
    def unique_tracks(cls, tracks: list[Track]) -> list[Track]:
        ids = [track.id for track in tracks]
        if len(set(ids)) != len(ids) or "overig" in ids or any(not item.strip() for item in ids):
            raise ValueError("track ids must be unique, nonempty and not 'overig'")
        return tracks


def public_preferences(preferences: dict) -> dict:
    """Only matching wishes, never free-form notes/evidence or home coordinates."""
    return {
        "tracks": [
            {key: item[key] for key in ("id", "name", "weight", "keywords") if key in item}
            for item in preferences.get("tracks", [])
        ],
        "likes": [
            {key: item[key] for key in ("tag", "weight") if key in item}
            for item in preferences.get("likes", [])
        ],
        "dislikes": [
            {key: item[key] for key in ("tag", "weight") if key in item}
            for item in preferences.get("dislikes", [])
        ],
        "hard_filters": {
            key: value
            for key, value in preferences.get("hard_filters", {}).items()
            if key
            in (
                "hours_min",
                "hours_max",
                "no_phone_main",
                "no_structural_evenings",
                "no_heavy_physical",
                "day_work_only",
                "wants_guidance",
            )
        },
        "keywords": {
            key: value
            for key, value in (preferences.get("keywords") or {}).items()
            if key in ("boost", "veto")
        },
    }


def _neutral_preferences(data: dict) -> dict:
    """Never inherit the legacy account's home or restrictive defaults."""
    return {
        **data,
        "hard_filters": {
            "no_phone_main": False,
            "no_structural_evenings": False,
            **(data.get("hard_filters") or {}),
        },
        "home": {"label": "Nog niet ingesteld", "lat": 0, "lon": 0, **(data.get("home") or {})},
    }


def _validate_preferences(data: dict) -> Preferences:
    return Preferences.model_validate(_neutral_preferences(data))


def ensure_dirs() -> None:
    config.profile_dir().mkdir(parents=True, exist_ok=True)
    config.user_path(HISTORY_DIR).mkdir(parents=True, exist_ok=True)
    config.user_path(DRAFTS_DIR).mkdir(parents=True, exist_ok=True)


def profile_exists() -> bool:
    return config.user_path(PROFILE_FILE).exists()


def read_profile() -> str:
    stored = store.get_profile()
    if stored is not None:
        return stored
    path = config.user_path(PROFILE_FILE)
    return path.read_text() if path.exists() else ""


def read_answer_log() -> str:
    rows = store.list_answers()
    if rows:
        return "\n".join(
            f"## {row['created_at']}\n**Vraag:** {row['question']}\n\n"
            f"**Antwoord:** {row['answer']}\n"
            for row in rows
        )
    path = config.user_path(ANSWER_LOG_FILE)
    return path.read_text() if path.exists() else ""


def read_preferences() -> dict:
    data = store.get_preferences()
    if data is None:
        path = config.user_path(PREFERENCES_FILE)
        data = json.loads(path.read_text()) if path.exists() else {}
    return _validate_preferences(data).model_dump()


def seed_available() -> bool:
    return all((config.seed_dir() / name).exists() for name in config.seed_files().values())


def read_seed(key: str) -> str:
    name = config.seed_files().get(key)
    if not name:
        return ""
    path = config.seed_dir() / name
    if not path.exists():
        return ""
    if path.suffix.lower() == ".docx":
        return ""  # binary seed, used only by document generation
    return path.read_text(errors="replace")


def seed_status() -> dict:
    missing = [
        name for name in config.seed_files().values() if not (config.seed_dir() / name).exists()
    ]
    return {"dir": str(config.seed_dir()), "missing": missing, "ready": not missing}


def write_profile(markdown: str, note: str) -> Path:
    """Archive the previous version, then write the new canon."""
    ensure_dirs()
    previous = read_profile()
    if previous:
        digest = hashlib.sha256(previous.encode()).hexdigest()[:10]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archive = config.user_path(HISTORY_DIR) / f"{stamp}-{digest}.md"
        archive.write_text(previous)
        store.add_profile_version(str(archive), note)
    path = config.user_path(PROFILE_FILE)
    path.write_text(markdown.rstrip() + "\n")
    store.set_profile(markdown.rstrip() + "\n")
    return path


def append_answer(question: str, answer: str) -> None:
    """Deterministically record one interview exchange in the answer log."""
    ensure_dirs()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    question = " ".join(question.split())
    answer = " ".join(answer.split())
    with config.user_path(ANSWER_LOG_FILE).open("a") as handle:
        handle.write(f"\n## {stamp}\n**Vraag:** {question}\n\n**Antwoord:** {answer}\n")
    store.append_answer(question, answer)


def split_sections(markdown: str) -> list[dict]:
    """Split markdown on '## ' headings; the preamble becomes heading ''."""
    sections: list[dict] = []
    heading = ""
    lines: list[str] = []
    for line in (markdown or "").splitlines():
        if line.startswith("## "):
            if heading or any(part.strip() for part in lines):
                sections.append({"heading": heading, "text": "\n".join(lines).strip()})
            heading = line[3:].strip()
            lines = []
        else:
            lines.append(line)
    if heading or any(part.strip() for part in lines):
        sections.append({"heading": heading, "text": "\n".join(lines).strip()})
    return sections


def public_excerpt(markdown: str, max_chars: int = 6000) -> str:
    """Sections safe for letters/CVs/matching: no private or open-questions blocks."""
    parts: list[str] = []
    total = 0
    truncated = False
    for section in split_sections(markdown):
        heading = section["heading"]
        if heading.startswith(PRIVATE_HEADING) or heading in HIDDEN_HEADINGS:
            continue
        body = section["text"]
        if not body and not heading:
            continue
        chunk = f"## {heading}\n{body}".rstrip() if heading else body
        if total + len(chunk) > max_chars:
            truncated = True
            break
        parts.append(chunk)
        total += len(chunk)
    text = "\n\n".join(parts).strip()
    if truncated:
        text += "\n\n[profiel-excerpt afgekapt op veilige sectiegrens]"
    return text


def read_public_excerpt(max_chars: int = 6000) -> str:
    return public_excerpt(read_profile(), max_chars)


def answer_log_size() -> int:
    path = config.user_path(ANSWER_LOG_FILE)
    return path.stat().st_size if path.exists() else 0


def new_answer_log() -> str:
    """Only the answer-log text added since the last accepted consolidation."""
    if not config.user_path(ANSWER_LOG_FILE).exists():
        return ""
    size = answer_log_size()
    offset = int(store.meta_get("answer_log_offset", "0") or 0)
    if offset <= 0 or offset > size:
        return config.user_path(ANSWER_LOG_FILE).read_text()
    with config.user_path(ANSWER_LOG_FILE).open("rb") as handle:
        handle.seek(offset)
        return handle.read().decode("utf-8", "replace")


def mark_answer_log_consolidated() -> None:
    store.meta_set("answer_log_offset", str(answer_log_size()))


def write_preferences(data: dict) -> dict:
    ensure_dirs()
    model = _validate_preferences(data)
    config.user_path(PREFERENCES_FILE).write_text(
        json.dumps(model.model_dump(), indent=2, ensure_ascii=False) + "\n"
    )
    store.set_preferences(model.model_dump())
    return model.model_dump()


def build_consolidation_prompt(full: bool = False) -> str:
    if tenancy.user_id() != tenancy.LEGACY_ID:
        return (
            "Maak een Nederlands profielvoorstel voor deze gebruiker, uitsluitend op basis van diens eigen antwoorden en profiel. "
            "Verzin geen persoonsgegevens, ervaring, beperkingen of voorkeuren. Ontbrekende gegevens blijven onbekend. "
            "Antwoord alleen JSON met profile_md (Markdown met ## koppen) en preferences volgens dit bestaande schema. "
            "Behoud privégegevens uitsluitend onder ## Privé. De gebruiker moet het voorstel zelf accepteren.\n"
            f"Schema en bestaande voorkeuren: {json.dumps(read_preferences(), ensure_ascii=False)}\n"
            f"Gebruiker: {tenancy.display_name()}\nProfiel: {read_profile()}\nAntwoorden: {read_answer_log()}"
        )
    template = (config.PROMPTS_DIR / "consolidation.md").read_text()
    incremental_ready = profile_exists() and store.meta_get("answer_log_offset") is not None
    if full or not incremental_ready:
        mode = "volledige bootstrap"
        answers = read_answer_log() or "(geen antwoorden)"
        dossier = read_seed("dossier") or "(ontbreekt)"
        sketch = read_seed("profile_sketch") or "(ontbreekt)"
        strategy = read_seed("strategy") or "(ontbreekt)"
    else:
        mode = "incrementele update"
        answers = new_answer_log() or "(geen nieuwe antwoorden sinds de laatste consolidatie)"
        note = "(al geïncorporeerd in het bestaande profiel; niet opnieuw toevoegen)"
        dossier = sketch = strategy = note
    return (
        template.replace("{{MODE}}", mode)
        .replace("{{PROFILE_MD}}", read_profile() or "(nog geen profiel)")
        .replace("{{ANSWER_LOG}}", answers)
        .replace("{{DOSSIER}}", dossier)
        .replace("{{PROFILE_SKETCH}}", sketch)
        .replace("{{STRATEGY}}", strategy)
    )


def save_raw_consolidation(text: str) -> Path:
    """Keep a failed consolidation reply for diagnosis/recovery."""
    ensure_dirs()
    stamp = now_local().strftime("%Y%m%dT%H%M%SZ")
    path = config.user_path(DRAFTS_DIR) / f"consolidation-{stamp}.txt"
    path.write_text(text)
    return path


def save_draft(profile_md: str, preferences: dict, meta: dict | None = None) -> Path:
    """Persist a consolidation proposal; nothing becomes canon until accepted."""
    ensure_dirs()
    stamp = now_local().strftime("%Y%m%dT%H%M%S")
    path = config.user_path(DRAFTS_DIR) / f"consolidation-{stamp}.json"
    payload = {
        "status": "pending",
        "created_at": now_iso(),
        "profile_md": profile_md.rstrip() + "\n",
        "preferences": _validate_preferences(preferences).model_dump(),
        **(meta or {}),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    store.meta_set("consolidation_draft", str(path))
    return path


def pending_draft() -> tuple[Path, dict] | None:
    raw = store.meta_get("consolidation_draft")
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw))
    if config.user_path(DRAFTS_DIR).exists():
        candidates.extend(
            sorted(config.user_path(DRAFTS_DIR).glob("consolidation-*.json"), reverse=True)
        )
    seen: set[Path] = set()
    for path in candidates:
        if (
            not path.exists()
            or path in seen
            or not path.resolve().is_relative_to(config.user_path(DRAFTS_DIR).resolve())
        ):
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("status") == "pending":
            store.meta_set("consolidation_draft", str(path))
            return path, data
    return None


def update_draft(path: Path, profile_md: str, preferences: dict) -> dict:
    data = json.loads(path.read_text())
    if data.get("status") != "pending":
        raise ValueError("dit voorstel is niet meer open")
    data["profile_md"] = profile_md.rstrip() + "\n"
    data["preferences"] = _validate_preferences(preferences).model_dump()
    data["edited_at"] = now_iso()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def accept_draft(path: Path) -> dict:
    data = json.loads(path.read_text())
    if data.get("status") != "pending":
        raise ValueError("dit voorstel is niet meer open")
    write_profile(data["profile_md"], note="consolidation accepted")
    write_preferences(data["preferences"])
    mark_answer_log_consolidated()
    data.update(status="accepted", accepted_at=now_iso())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    store.meta_set("bootstrap_status", "done")
    store.meta_set("profile_bootstrapped_at", now_iso())
    store.meta_set("consolidation_draft", "")
    return data


def discard_draft(path: Path) -> None:
    data = json.loads(path.read_text())
    data.update(status="discarded", discarded_at=now_iso())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    if store.meta_get("consolidation_draft") == str(path):
        store.meta_set("consolidation_draft", "")
    store.meta_set("bootstrap_status", "idle")


def parse_consolidation(text: str) -> dict:
    data = parse_json_object(text)
    if "profile_md" not in data or "preferences" not in data:
        raise ValueError("JSON is missing profile_md or preferences")
    _validate_preferences(data["preferences"])
    return data
