"""A local, CPU-only meaning index of every vacancy: one shared file, no tokens, no API.

Each vacancy is reduced to the words that say something (no stop words, nothing that
appears in more than one in five vacancies) and turned into a vector by a static
model2vec model. Static models average word vectors, so generic job-ad words would
only blur the meaning; dropping them makes the vectors sharper and faster.

Only the worker uses the model, each time in a short-lived child process
(`embed_child.py`), so its memory is returned at once. The web process reads the stored
vectors, so a vote can re-rank everything in milliseconds. Without a model the index
is simply absent and ranking works as before.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from . import config, feedback, memory, store

log = logging.getLogger("jobcraft")

DEFAULT_MODEL = "/opt/models/potion-multilingual-128M"
COMMON_SHARE = 0.2
MAX_WORDS = 300
WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\-]+")
COUNTED = re.compile(r"[a-zà-ÿ]{4,}")  # the words feedback counts per vacancy

_model_lock = threading.Lock()  # one child at a time: each needs about 1.8 GB
_cache: dict = {}
ROOT = Path(__file__).resolve().parent.parent


def model_path() -> str:
    return os.environ.get("EMBED_MODEL", DEFAULT_MODEL).strip()


def model_available() -> bool:
    """A local folder in a process that may run it: the web container has too little memory."""
    path = model_path()
    if os.environ.get("APP_ROLE", "all").strip() == "web":
        return False
    return bool(path) and path != "0" and Path(path, "model.safetensors").exists()


def _index_file() -> Path:
    return config.DATA_DIR / "index" / "vacancies.npz"


def embed(texts: list[str]) -> np.ndarray:
    """Unit vectors, one row per text, made by the model in a child process."""
    with _model_lock:
        result = subprocess.run(
            [sys.executable, "-m", "app.embed_child", model_path()],
            input=json.dumps(texts).encode(),
            capture_output=True,
            check=True,
            timeout=900,
            cwd=ROOT,
        )
    vectors = np.load(io.BytesIO(result.stdout), allow_pickle=False)
    return vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)


def key_text(title: str | None, description: str | None, df: dict[str, int], total: int) -> str:
    """The title plus the words that tell this vacancy apart from the others."""
    words = []
    for word in WORD.findall(description or ""):
        low = word.lower()
        if COUNTED.fullmatch(low):
            count = df.get(low, 0)
            if low in feedback.STOPWORDS or count < 2 or (total and count / total > COMMON_SHARE):
                continue
        elif not (word.isupper() and 2 <= len(word) <= 6):  # keep ICT, HR, BIM and the like
            continue
        words.append(word)
        if len(words) >= MAX_WORDS:
            break
    return f"{title or ''}. {' '.join(words)}".strip()


def load() -> tuple[np.ndarray, np.ndarray] | None:
    """(job ids, unit vectors as float32), cached until the file changes."""
    path = _index_file()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    if _cache.get("stamp") != stamp:
        with np.load(path, allow_pickle=False) as data:
            _cache.update(
                stamp=stamp,
                ids=data["ids"].astype(np.int64),
                hashes=data["hashes"],
                vectors=data["vectors"].astype(np.float32),
            )
    return _cache["ids"], _cache["vectors"]


def _save(ids: np.ndarray, hashes: np.ndarray, vectors: np.ndarray) -> None:
    path = _index_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.npz")
    np.savez(temporary, ids=ids, hashes=hashes, vectors=vectors.astype(np.float16))
    os.replace(temporary, path)


def refresh() -> int:
    """Index new or changed vacancies and forget removed ones; returns how many were embedded."""
    if not model_available():
        return 0
    with memory.catalogue():
        return _refresh()


def _refresh() -> int:
    with store.connect() as con:
        rows = con.execute("SELECT id, title, description, content_hash FROM jobs").fetchall()
    known: dict[int, tuple[str, np.ndarray]] = {}
    if load() is not None:
        known = {
            int(job_id): (str(digest), vector)
            for job_id, digest, vector in zip(_cache["ids"], _cache["hashes"], _cache["vectors"], strict=True)
        }
    todo = [row for row in rows if known.get(row["id"], ("",))[0] != (row["content_hash"] or "")]
    present = {row["id"] for row in rows}
    if not todo and set(known) == present:
        return 0
    df, total = feedback._document_frequency()
    vectors = embed([key_text(row["title"], row["description"], df, total) for row in todo]) if todo else []
    fresh = {
        row["id"]: (row["content_hash"] or "", vector) for row, vector in zip(todo, vectors, strict=True)
    }
    merged = {job_id: value for job_id, value in known.items() if job_id in present} | fresh
    if merged:
        order = sorted(merged)
        _save(
            np.asarray(order, dtype=np.int64),
            np.asarray([merged[job_id][0] for job_id in order]),
            np.stack([merged[job_id][1] for job_id in order]),
        )
    log.info("vacancy index: %d embedded, %d in total", len(todo), len(merged))
    return len(todo)
