"""Five for today: quick answers where they teach the most, and the reason behind a "nee"."""

from __future__ import annotations

import json

from test_jobs_page import client  # noqa: F401

from app import judgements, store


def add(title, company, rank, signals=None, verdict=None):
    job_id, _ = store.upsert_job(
        {
            "source_id": "test",
            "url": f"https://example.test/{title}",
            "title": title,
            "company": company,
            "description": "Buiten werken in het groen",
            "location": "unknown",
            "posted_at": "2026-10-01",
            "content_hash": title,
        }
    )
    store.set_rank_scores({job_id: rank})
    if signals:
        store.update_job_signals(job_id, rank, json.dumps(signals), "key", signals.get("confidence", 0.9))
    if verdict is not None:
        store.update_job_match(job_id, verdict, "", "stale")
    return job_id


def test_the_round_asks_the_best_then_where_jev_is_least_sure(client, monkeypatch):  # noqa: F811
    best = add("Hovenier", "A", 95, {"fit": 80, "confidence": 0.9, "escalate": False})
    unsure = add("Tuinman", "B", 80, {"fit": 50, "confidence": 0.4, "escalate": True})
    less_unsure = add("Boomverzorger", "C", 70, {"fit": 60, "confidence": 0.6, "escalate": True})
    same_company = add("Groenwerker", "A", 90, {"fit": 50, "confidence": 0.3, "escalate": True})
    filler = add("Hovenier II", "D", 60, {"fit": 20, "confidence": 0.95, "escalate": False})
    picked = judgements.pick(set())
    assert picked[:3] == [best, unsure, less_unsure]
    assert same_company not in picked  # one employer once
    assert filler in picked
    assert best not in judgements.pick({best})


def test_a_disagreement_between_jev_and_the_matcher_is_asked(client, monkeypatch):  # noqa: F811
    add("Hovenier", "A", 95)
    split = add("Tuinman", "B", 40, {"fit": 20, "confidence": 0.9, "escalate": False}, verdict=80)
    agree = add("Boomverzorger", "C", 50, {"fit": 80, "confidence": 0.9, "escalate": False}, verdict=85)
    monkeypatch.setattr(judgements.ranking, "verdict_is_fresh", lambda job, digest: job["match_score"] is not None)
    assert judgements._disagree(store.get_job(split), "d") == 60
    assert judgements._disagree(store.get_job(agree), "d") == 0
    assert judgements.pick(set())[1] == split


def test_answers_vote_move_on_and_ask_why_after_a_no(client):  # noqa: F811
    first = add("Hovenier", "A", 95)
    second = add("Tuinman", "B", 80)
    third = add("Boomverzorger", "C", 70)
    page = client.get("/").text
    assert "Vijf voor vandaag" in page and "Vacature 1 van 3" in page
    assert f'action="/vandaag/oordeel/{first}"' in page

    answered = client.post(f"/vandaag/oordeel/{first}", data={"answer": "nee"}, follow_redirects=False)
    assert answered.headers["location"] == "/#vijf"
    assert store.get_job(first)["vote"] == -1
    assert judgements._record(first) | {"at": ""} == {"answer": "nee", "asked": True, "rank_before": 95, "at": ""}
    page = client.get("/").text
    assert "Waarom past Hovenier niet?" in page and "Vacature 2 van 3" in page

    reason = client.post(f"/jobs/{first}/reden", data={"reason": "ver", "next": "/"}, follow_redirects=False)
    assert reason.headers["location"] == "/#vijf"
    assert judgements.reason_of(first) == "ver"
    assert "Waarom past Hovenier niet?" not in client.get("/").text

    client.post(f"/vandaag/oordeel/{second}", data={"answer": "twijfel"})
    assert not store.get_job(second)["vote"]
    client.post(f"/vandaag/oordeel/{third}", data={"answer": "ja"})
    assert store.get_job(third)["vote"] == 1
    assert "Klaar voor vandaag" in client.get("/").text
    assert second not in judgements.pick(set(judgements._state()["skipped"]))


def test_a_reason_only_for_a_vacancy_turned_down_and_only_one_we_offer(client):  # noqa: F811
    job_id = add("Hovenier", "A", 95)
    assert client.post(f"/jobs/{job_id}/reden", data={"reason": "ver"}).status_code == 400
    store.update_job_status(job_id, "ignored")
    assert client.post(f"/jobs/{job_id}/reden", data={"reason": "iets anders"}).status_code == 400
    assert client.post(f"/vandaag/oordeel/{job_id}", data={"answer": "misschien"}).status_code == 400
    done = client.post(f"/jobs/{job_id}/reden", data={"reason": "uren", "next": f"/jobs/{job_id}"}, follow_redirects=False)
    assert done.headers["location"] == f"/jobs/{job_id}?gedaan=reden#acties"


def test_the_vacancy_page_asks_why_after_not_for_me(client):  # noqa: F811
    job_id = add("Hovenier", "A", 95)
    assert "Past dit bij je?" in client.get(f"/jobs/{job_id}").text
    client.post(f"/jobs/{job_id}/status", data={"status": "ignored", "next": f"/jobs/{job_id}"})
    page = client.get(f"/jobs/{job_id}?gedaan=ignored").text
    assert f'action="/jobs/{job_id}/reden"' in page
    assert f'action="/jobs/{job_id}/reden"' not in client.get(f"/jobs/{job_id}?gedaan=saved").text


def test_not_for_me_on_vandaag_goes_through_the_same_answer(client):  # noqa: F811
    add("Hovenier", "A", 95)
    other = add("Tuinman", "B", 80)
    page = client.get("/").text
    assert page.count(f'action="/vandaag/oordeel/{other}"') == 1  # in Nieuw en kansrijk, not asked twice
    client.post(f"/vandaag/oordeel/{other}", data={"answer": "nee"})
    assert store.get_job(other)["vote"] == -1 and "Waarom past Tuinman niet?" in client.get("/").text
