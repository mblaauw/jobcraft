"""Structured interview coverage: deterministic signals + manual overrides.

Open topics are injected into the interviewer context; coverage is no longer
written to a separate plan file. Keywords are transparent heuristics, not
semantic understanding; the UI allows manual correction.
"""

from __future__ import annotations

import json
import threading

from . import config, store, tenancy
from .util import now_iso

STATE_FILE = config.PROFILE_DIR / "interview-state.json"
PLAN_FILE = config.PROFILE_DIR / "interview-plan.md"
ANSWER_LOG_FILE = config.PROFILE_DIR / "answer-log.md"
_LOCK = threading.Lock()
COVERED_THRESHOLD = 2

TOPIC_SPECS = [
    {
        "id": "werk_energie",
        "label": "Dagelijkse werkzaamheden die energie geven",
        "keywords": [
            "energie",
            "fijn",
            "leuk",
            "goed",
            "werkdag",
            "bezig",
            "voldoening",
            "blij",
            "zin",
        ],
    },
    {
        "id": "werk_afknappers",
        "label": "Werkzaamheden en omstandigheden die energie kosten",
        "keywords": [
            "kost",
            "moe",
            "vervelend",
            "stress",
            "druk",
            "saai",
            "irrit",
            "afknapper",
            "lies",
        ],
    },
    {
        "id": "uren_ritme",
        "label": "Uren, ritme en voorspelbaarheid",
        "keywords": [
            "uur",
            "uren",
            "parttime",
            "fulltime",
            "rooster",
            "dagdienst",
            "avond",
            "weekend",
            "ritme",
        ],
    },
    {
        "id": "omgeving",
        "label": "Binnen/buiten, lawaai, sociale intensiteit",
        "keywords": [
            "buiten",
            "binnen",
            "kantoor",
            "lawaai",
            "rustig",
            "druk",
            "mensen",
            "team",
            "alleen",
        ],
    },
    {
        "id": "autonomie",
        "label": "Autonomie versus begeleiding",
        "keywords": [
            "zelfstandig",
            "autonomie",
            "begeleiding",
            "inwerk",
            "leiding",
            "vrijheid",
            "controle",
        ],
    },
    {
        "id": "fysiek",
        "label": "Fysieke belasting",
        "keywords": ["fysiek", "tillen", "lichamelijk", "zwaar", "staan", "lopen", "bewegen"],
    },
    {
        "id": "reistijd",
        "label": "Reistijd, fiets/OV, bedrijfsauto",
        "keywords": ["reis", "fiets", "ov", "trein", "auto", "bedrijfsauto", "afstand", "reistijd"],
    },
    {
        "id": "inkomen",
        "label": "Inkomen en zekerheid",
        "keywords": ["salaris", "inkomen", "loon", "contract", "zekerheid", "vast", "betaling"],
    },
    {
        "id": "groei",
        "label": "Leren, groei en ontwikkelpad",
        "keywords": ["leren", "groei", "opleiding", "ontwikkel", "doorgroei", "cursus", "ervaring"],
    },
    {
        "id": "afknappers",
        "label": "Afknappers: sales, telefonie, nachten/weekenden, zwaar werk, vlees",
        "keywords": [
            "sales",
            "bellen",
            "telefoon",
            "callcenter",
            "nacht",
            "weekend",
            "vlees",
            "zwaar",
        ],
    },
    {
        "id": "cultuur",
        "label": "Betekenis, teamcultuur en leiderschap",
        "keywords": [
            "cultuur",
            "collega",
            "leiderschap",
            "betekenis",
            "waarden",
            "sfeer",
            "respect",
        ],
    },
]
TOPIC_MAP = {topic["id"]: topic for topic in TOPIC_SPECS}


def _specs():
    if tenancy.user_id() == tenancy.LEGACY_ID:
        return TOPIC_SPECS
    labels = {
        "uren_ritme": "Gewenste uren en werkritme",
        "afknappers": "Persoonlijke grenzen en afknappers",
    }
    return [{**spec, "label": labels.get(spec["id"], spec["label"])} for spec in TOPIC_SPECS]


def _new_state() -> dict:
    return {
        "updated_at": now_iso(),
        "question_count": 0,
        "topics": [
            {
                "id": spec["id"],
                "label": spec["label"],
                "status": "open",
                "mentions": 0,
                "last_touched": None,
                "manual": False,
            }
            for spec in _specs()
        ],
    }


def _load() -> dict:
    state = store.get_interview_state()
    if state is None:
        state = json.loads(config.user_path(STATE_FILE).read_text())
    known = {topic["id"] for topic in state.get("topics", [])}
    for spec in _specs():
        if spec["id"] not in known:
            state["topics"].append(
                {
                    "id": spec["id"],
                    "label": spec["label"],
                    "status": "open",
                    "mentions": 0,
                    "last_touched": None,
                    "manual": False,
                }
            )
    return state


def _save(state: dict) -> None:
    state["updated_at"] = now_iso()
    config.user_path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    config.user_path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2))
    store.set_interview_state(state)


def open_topics() -> list[str]:
    """Labels the interviewer still needs to cover; fed into its context."""
    return [topic["label"] for topic in summary()["topics"] if topic["status"] != "covered"]


def _apply_log(state: dict) -> None:
    if not config.user_path(ANSWER_LOG_FILE).exists():
        return
    text = config.user_path(ANSWER_LOG_FILE).read_text().lower()
    for topic in state["topics"]:
        spec = TOPIC_MAP.get(topic["id"])
        if not spec:
            continue
        mentions = sum(1 for keyword in spec["keywords"] if keyword in text)
        topic["mentions"] = mentions
        if mentions >= COVERED_THRESHOLD:
            topic["status"] = "covered"


def ensure_state(seed_from_log: bool = True) -> dict:
    with _LOCK:
        config.user_path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
        if config.user_path(STATE_FILE).exists():
            return _load()
        state = _new_state()
        if seed_from_log:
            _apply_log(state)
        _save(state)
        return state


def record_answer(question: str, answer: str) -> dict:
    """Update keyword signals after one interview answer (cheap, transparent)."""
    ensure_state()
    with _LOCK:
        state = _load()
        text = answer.lower()
        for topic in state["topics"]:
            spec = TOPIC_MAP.get(topic["id"])
            if not spec:
                continue
            hits = sum(1 for keyword in spec["keywords"] if keyword in text)
            if hits:
                topic["mentions"] = int(topic.get("mentions") or 0) + hits
                topic["last_touched"] = now_iso()
                if int(topic["mentions"]) >= COVERED_THRESHOLD:
                    topic["status"] = "covered"
        state["question_count"] = int(state.get("question_count") or 0) + 1
        _save(state)
        return state


def toggle(topic_id: str, status: str) -> dict:
    if status not in ("covered", "open"):
        raise ValueError("unknown coverage status")
    ensure_state()
    with _LOCK:
        state = _load()
        topic = next((item for item in state["topics"] if item["id"] == topic_id), None)
        if topic is None:
            raise ValueError("unknown topic")
        topic["status"] = status
        topic["manual"] = True
        topic["last_touched"] = now_iso()
        _save(state)
        return state


def summary() -> dict:
    state = ensure_state()
    covered = sum(1 for topic in state["topics"] if topic["status"] == "covered")
    return {
        "topics": state["topics"],
        "covered": covered,
        "total": len(state["topics"]),
        "question_count": state.get("question_count", 0),
        "updated_at": state.get("updated_at"),
    }
