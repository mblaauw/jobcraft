"""The local meaning index and the taste it gives each account, with a fake model."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from app import config, job_index, profile, ranking, store, taste

GREEN = "Hovenier groenvoorziening snoeien tuinen plantsoenen bomen beplanting"
DATA = "Data analist SQL dashboards Python rapportages datamodellen Power BI"


def fake_embed(texts):
    """Words hashed into a small space: similar texts get similar vectors."""
    vectors = np.zeros((len(texts), 64), dtype=np.float32)
    for row, text in enumerate(texts):
        for word in text.lower().split():
            vectors[row, int(hashlib.md5(word.strip(".,").encode()).hexdigest(), 16) % 64] += 1
    return vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    store.init_db()
    monkeypatch.setattr(job_index, "model_available", lambda: True)
    monkeypatch.setattr(job_index, "embed", fake_embed)
    job_index._cache.clear()
    yield
    job_index._cache.clear()


def add(title, text, number):
    job_id, _ = store.upsert_job(
        {
            "source_id": "test",
            "url": f"https://example.test/{number}",
            "title": title,
            "company": f"Werkgever {number}",
            "description": f"{text} vacature {number}",
            "location": "Utrecht",
            "content_hash": f"{title}-{number}",
        }
    )
    return job_id


def test_key_text_keeps_what_sets_a_vacancy_apart():
    df = {"hovenier": 3, "werkzaamheden": 90, "uniek": 1}
    text = job_index.key_text("Hovenier", "De hovenier doet werkzaamheden in ICT en uniek werk", df, 100)
    assert "hovenier" in text and "ICT" in text
    assert "werkzaamheden" not in text and "uniek" not in text and " doet" not in text


def test_the_index_only_embeds_what_changed_and_forgets_what_is_gone(world):
    first = add("Hovenier", GREEN, 1)
    add("Data analist", DATA, 2)
    assert job_index.refresh() == 2
    assert job_index.refresh() == 0
    ids, vectors = job_index.load()
    assert sorted(ids.tolist()) == sorted([first, first + 1]) and vectors.shape == (2, 64)
    with store.connect() as con:
        con.execute("UPDATE jobs SET content_hash='changed' WHERE id=?", (first,))
    assert job_index.refresh() == 1
    with store.connect() as con:
        con.execute("DELETE FROM jobs WHERE id=?", (first,))
    job_index.refresh()
    assert job_index.load()[0].tolist() == [first + 1]


def test_without_a_model_nothing_changes(world, monkeypatch):
    monkeypatch.setattr(job_index, "model_available", lambda: False)
    add("Hovenier", GREEN, 1)
    assert job_index.refresh() == 0 and job_index.load() is None
    assert taste.scores(store.list_jobs_for_matching(), {}, lambda job: False) == {}


def test_what_you_look_for_lifts_similar_vacancies_without_any_vote(world):
    green = [add("Hovenier", GREEN, n) for n in range(3)]
    data = [add("Data analist", DATA, n + 10) for n in range(3)]
    job_index.refresh()
    preferences = profile.read_preferences()
    preferences["tracks"] = [{"id": "groen", "label": "Hovenier", "keywords": ["groenvoorziening", "snoeien"]}]
    scores = taste.scores(store.list_jobs_for_matching(), preferences, lambda job: False)
    assert min(scores[i] for i in green) > max(scores[i] for i in data)


def test_a_thumbs_down_pulls_similar_vacancies_down_at_once(world):
    green = [add("Hovenier", GREEN, n) for n in range(4)]
    data = [add("Data analist", DATA, n + 10) for n in range(4)]
    job_index.refresh()
    ranking.rerank_all()
    before = {row["id"]: row["rank_score"] for row in store.list_jobs_for_matching()}
    for job_id in data[:2]:
        store.set_job_vote(job_id, -1)
    store.set_job_vote(green[0], 1)
    ranking.after_vote(data[1])
    after = {row["id"]: row["rank_score"] for row in store.list_jobs_for_matching()}
    assert after[data[0]] <= 10 and after[green[0]] >= 80
    assert after[data[3]] < before[data[3]]
    assert min(after[i] for i in green[1:]) > max(after[i] for i in data[2:])


def test_only_the_worker_may_run_the_model(tmp_path, monkeypatch):
    (tmp_path / "model.safetensors").write_bytes(b"")
    monkeypatch.setenv("EMBED_MODEL", str(tmp_path))
    monkeypatch.setenv("APP_ROLE", "web")
    assert not job_index.model_available()
    monkeypatch.setenv("APP_ROLE", "worker")
    assert job_index.model_available()
    monkeypatch.setenv("EMBED_MODEL", "0")
    assert not job_index.model_available()
