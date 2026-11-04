"""The round follows the route: unsure jobs are judged, sure ones only as a small sample."""

from __future__ import annotations

import json

from test_jobs_page import client  # noqa: F401

from app import matching, ranking, store, systemone
from app.scoring.digests import job_match_digest

DIGEST = "d"
SURE = {"fit": 80, "confidence": 0.9, "escalate": False}
UNSURE = {"fit": 50, "confidence": 0.4, "escalate": True}


def add(title, rank, signals=None, fresh=False, vote=0):
    job_id, _ = store.upsert_job(
        {
            "source_id": "test",
            "url": f"https://example.test/{title}",
            "title": title,
            "company": title,
            "description": "Buiten werken in het groen",
            "location": "unknown",
            "posted_at": "2026-10-01",
            "content_hash": title,
        }
    )
    store.set_rank_scores({job_id: rank})
    if signals:
        store.update_job_signals(job_id, rank, json.dumps(signals), "key", signals["confidence"])
    if fresh:
        store.update_job_match(job_id, 70, "", job_match_digest(DIGEST, store.get_job(job_id)))
    if vote:
        store.set_job_vote(job_id, vote)
    return job_id


def test_candidates_are_the_unsure_above_the_floor_best_first(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    low = add("Laag", 80, UNSURE)
    high = add("Hoog", 90, UNSURE)
    add("Zeker", 95, SURE)
    add("Onder de grens", 20, UNSURE)
    add("Al beoordeeld", 85, UNSURE, fresh=True)
    add("Gestemd", 85, UNSURE, vote=1)
    add("Zonder signalen", 85)
    assert [job["id"] for job in ranking.escalation_candidates(DIGEST, 10)] == [high, low]
    assert [job["id"] for job in ranking.escalation_candidates(DIGEST, 1)] == [high]


def test_without_jev_a_vacancy_without_signals_still_reaches_the_matcher(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(systemone, "enabled", lambda: False)
    plain = add("Zonder signalen", 85)
    facts_only = add("Alleen feiten", 80, {"fit": None, "confidence": 0.0, "escalate": True})
    add("Zonder signalen, laag", 20)
    assert [job["id"] for job in ranking.escalation_candidates(DIGEST, 10)] == [plain, facts_only]
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    assert ranking.escalation_candidates(DIGEST, 10) == []  # Jev answers first, cheaply
    assert ranking.route_counts(DIGEST)["wait"] == 3


def test_the_sample_takes_the_best_sure_fits_and_can_be_switched_off(client, monkeypatch):  # noqa: F811
    add("Twijfel", 90, UNSURE)
    second = add("Zeker twee", 85, SURE)
    first = add("Zeker een", 95, SURE)
    add("Zeker drie", 70, SURE)
    add("Zeker maar laag", 20, SURE)
    add("Zeker en beoordeeld", 99, SURE, fresh=True)
    assert [job["id"] for job in ranking.calibration_sample(DIGEST)] == [first, second]
    monkeypatch.setenv("MATCH_SAMPLE", "0")
    assert ranking.calibration_sample(DIGEST) == []


def test_route_counts_cover_every_outcome(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    add("Twijfel", 90, UNSURE)
    add("Zeker", 95, SURE)
    add("Nieuw", 50)
    add("Klaar", 85, UNSURE, fresh=True)
    add("Weg", 85, UNSURE, vote=-1)
    assert ranking.route_counts(DIGEST) == {"excluded": 1, "verdict": 1, "wait": 1, "act": 1, "escalate": 1}
    store.meta_set("match_summary", json.dumps({"matched": 1, "signals": 0, "sampled": 1, "routes": ranking.route_counts(DIGEST)}))
    page = client.get("/jobs").text
    assert "waarvan 1 als steekproef" in page
    assert "1 niet van toepassing, 1 uitgebreid beoordeeld, 1 nog geen snelle beoordeling, 1 snelle beoordeling volstaat, 1 uitgebreide beoordeling nodig." in page


def test_a_round_judges_the_unsure_and_the_sample_inside_one_batch(client, monkeypatch):  # noqa: F811
    for title, rank in (("Twijfel a", 90), ("Twijfel b", 80), ("Twijfel c", 70)):
        add(title, rank, UNSURE)
    sure = add("Zeker", 95, SURE)
    monkeypatch.setattr(systemone, "enabled", lambda: False)
    monkeypatch.setattr(matching, "current_digest", lambda *args: DIGEST)
    monkeypatch.setattr(matching, "verdict_prompt", lambda *args: "Beoordeel deze vacature.")
    monkeypatch.setattr(matching.ranking, "rerank_all", lambda *args, **kwargs: 0)
    monkeypatch.setenv("MATCH_SAMPLE", "5")  # more than the batch: the sample never exceeds it
    judged = []

    class Fake:
        client = object()

        def generate(self, prompt, **kwargs):
            judged.append(prompt)
            return json.dumps({"score": 60, "why": "ok", "concerns": [], "questions_for_employer": []}), {}

    summary = matching.match_jobs(Fake(), limit=3)
    assert (summary["escalated"], summary["sampled"], summary["matched"]) == (2, 1, 3)
    assert store.get_job(sure)["match_score"] == 60
    assert summary["routes"]["escalate"] == 1 and summary["routes"]["verdict"] == 3
