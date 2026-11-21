"""The ranking keeps what was paid for, and does not bring back what was turned down."""

from __future__ import annotations

import json

import numpy as np

from app import job_index, judgements, profile, ranking, store, taste
from app.scoring import digests
from tests.test_jobs_page import add_job, client  # noqa: F401

PREFS = {
    "tracks": [{"id": "groen", "name": "Groen", "weight": 1.0, "keywords": ["hovenier"]}],
    "home": {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041, "transit_minutes": 35, "bike_minutes": 30},
    "notes": "eerste aantekening",
    "likes": [{"tag": "buiten", "weight": 1.0, "evidence": "zei het in het gesprek"}],
}


def test_reach_notes_and_evidence_do_not_change_the_verdict_key():
    before = digests.profile_hash(PREFS)
    edited = json.loads(json.dumps(PREFS))
    edited["home"].update(transit_minutes=60, bike_minutes=0, car_minutes=45)
    edited["notes"] = "iets anders"
    edited["likes"][0]["evidence"] = "aangepast"
    assert digests.profile_hash(edited) == before
    moved = json.loads(json.dumps(PREFS))
    moved["home"].update(label="Utrecht", lat=52.0907, lon=5.1214)
    assert digests.profile_hash(moved) != before  # the prompt states the travel time from home
    other = json.loads(json.dumps(PREFS))
    other["tracks"][0]["keywords"].append("boomverzorger")
    assert digests.profile_hash(other) != before


def test_verdicts_bought_under_the_old_key_stay_current(client):  # noqa: F811
    profile.write_preferences(PREFS)
    prefs = profile.read_preferences()
    job_id = add_job("Hovenier")
    excerpt = digests.public_profile_digest()
    import hashlib

    old_base = hashlib.sha256(
        f"{digests._profile_hash_until_2026_11(prefs)}:{excerpt}:{digests.VERDICT_PROMPT_VERSION}".encode()
    ).hexdigest()[:20]
    store.update_job_match(job_id, 72, json.dumps({"why": "past"}), digests.job_match_digest(old_base, store.get_job(job_id)))
    now = digests.current_digest()  # the real one; the fixture replaces matching's
    assert ranking.verdict_is_fresh(store.get_job(job_id), now)
    # And after the reach slider moves, it still is.
    prefs["home"]["transit_minutes"] = 90
    profile.write_preferences(prefs)
    assert ranking.verdict_is_fresh(store.get_job(job_id), digests.current_digest())


def _same_vacancy_on_two_sites() -> tuple[int, int]:
    ids = []
    for site in ("a", "b"):
        job_id, _ = store.upsert_job({"source_id": "test", "url": f"https://{site}.test/1", "title": "Hovenier",
                                      "company": "Groenbedrijf", "location": "unknown", "description": "Buiten werken", "content_hash": site})
        store.set_rank_scores({job_id: 80})
        ids.append(job_id)
    return ids[0], ids[1]


def test_a_rejected_vacancy_does_not_return_through_its_copy_on_another_site(client):  # noqa: F811
    first, _second = _same_vacancy_on_two_sites()
    assert len(store.list_jobs_page(10, 0, "new", only_unvoted=True)) == 1
    store.set_job_vote(first, -1)
    assert store.list_jobs_page(10, 0, "new", only_unvoted=True) == []
    assert store.job_status_counts(only_unvoted=True) == {}
    # "New since" is asked of the folded vacancy too, in the count as in the list.
    store.set_job_vote(first, 0)
    with store.connect() as con:
        con.execute("UPDATE jobs SET first_seen='2020-01-01T00:00:00+00:00' WHERE id=?", (first,))
        con.execute("UPDATE job_states SET rank_score=95 WHERE job_id=?", (first,))
    since = {"seen_after": "2026-01-01T00:00:00+00:00"}
    assert store.job_status_counts(list_filters=since) == {} and store.list_jobs_page(10, 0, list_filters=since) == []
    assert [row["id"] for row in store.list_jobs_page(10, 0)] == [first]  # the list still shows the one you judged


def test_todays_round_is_picked_again_when_it_was_empty(client):  # noqa: F811
    assert judgements.today_round()["job"] is None  # opened before any vacancy had arrived
    add_job("Hovenier", rank=80)
    assert judgements.today_round()["job"]["title"] == "Hovenier"


def test_taste_in_pieces_gives_the_same_scores(client, monkeypatch):  # noqa: F811
    rng = np.random.default_rng(3)
    vectors = rng.normal(size=(40, 16)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    monkeypatch.setattr(job_index, "load", lambda: (np.arange(1, 41), vectors))
    monkeypatch.setattr(taste, "wish_vector", lambda preferences: None)
    jobs = [{"id": index, "vote": (1 if index % 7 == 0 else -1 if index % 5 == 0 else 0), "status": "new", "match_score": None} for index in range(1, 41)]
    whole = taste.scores(jobs, {}, lambda job: False)
    monkeypatch.setattr(taste, "ROWS_AT_ONCE", 6)
    assert taste.scores(jobs, {}, lambda job: False) == whole and len(whole) == 40
