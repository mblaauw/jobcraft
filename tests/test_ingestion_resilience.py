"""A scan round that meets trouble: nothing good is overwritten, nothing dead is kept alive."""

from __future__ import annotations

import json

import httpx
import pytest

from app import geo, profile, store, tasks
from app.adapters import ats, common, eures
from app.scoring import signals
from tests.factories import add_job
from tests.test_eures import FakeEures, _detail
from tests.test_jobs_page import client  # noqa: F401

FULL = "Je coördineert het gebouwbeheer en de ontvangst. " * 30


def test_a_feed_stub_does_not_replace_the_stored_full_text():
    url = "https://example.test/jobs/1"
    job_id = add_job(1, description=FULL)
    before = store.get_job(job_id)["content_hash"]
    store.upsert_job({"source_id": "fixture", "url": url, "title": "Fixture vacancy 1", "description": "Rijksoverheid · Den Haag", "content_hash": "stub"})
    stored = store.get_job(job_id)
    assert stored["description"] == FULL and stored["content_hash"] != "stub"
    # A real, changed text still replaces it.
    store.upsert_job({"source_id": "fixture", "url": url, "title": "Fixture vacancy 1", "description": FULL + " Nieuw: ook de inkoop.", "content_hash": "new"})
    assert store.get_job(job_id)["description"].endswith("ook de inkoop.") and before != "new"


def test_a_vacancy_with_several_offices_is_judged_by_the_nearest():
    home = {"label": "Utrecht", "lat": 52.0907, "lon": 5.1214, "transit_minutes": 35, "bike_minutes": 30}
    both = geo.assess("Utrecht, Amsterdam", home_settings=geo._settings_cached(json.dumps(home, sort_keys=True)))
    assert both["place"] == "Utrecht" and not both["too_far"]
    # A sentence that merely mentions two towns is not a list of offices.
    assert geo._listed_places("Standplaats Amsterdam, soms in Utrecht aanwezig") == []
    assert geo._listed_places("Zwolle / Deventer en Kampen") == ["zwolle", "deventer", "kampen"]


def test_a_pass_asked_for_while_one_runs_follows_it():
    first = tasks.enqueue("rerank", owner="a", dedupe_key="rerank:a")
    assert tasks.claim_next()["id"] == first
    assert tasks.enqueue("rerank", owner="a", dedupe_key="rerank:a") == first  # e.g. a vote during the pass
    assert tasks.open_count() == 1
    tasks.finish(first, {})
    follow_up = tasks.claim_next()
    assert follow_up and follow_up["kind"] == "rerank" and follow_up["id"] != first
    assert tasks.AGAIN not in json.loads(follow_up["payload_json"])
    tasks.finish(follow_up["id"], {})
    assert tasks.claim_next() is None  # one follow-up, not a loop


def test_other_kinds_are_not_repeated_after_a_second_request():
    first = tasks.enqueue("document", owner="a", dedupe_key="doc:a:1")
    tasks.claim_next()
    assert tasks.enqueue("document", owner="a", dedupe_key="doc:a:1") == first
    tasks.finish(first, {})
    assert tasks.claim_next() is None  # a second click must not write a paid document twice


class Response:
    def __init__(self, status):
        self.status_code = status


def test_a_site_that_keeps_answering_429_ends_the_source_early():
    class Limited:
        def get(self, url, **kwargs):
            return Response(429)

    entry: dict = {}
    for _ in range(common.DETAIL_FAILURE_LIMIT - 1):
        assert common.safe_detail_get(Limited(), "https://example.test/a", entry).status_code == 429
    with pytest.raises(httpx.HTTPError):
        common.safe_detail_get(Limited(), "https://example.test/a", entry)


def test_one_odd_board_among_many_does_not_fail_the_round(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ats.time, "sleep", lambda _: None)
    store.sync_sources([{"id": "x", "kind": "ats-registry"}])
    employers = [{"name": f"Werkgever {i}", "system": "lever", "slug": f"w{i}"} for i in range(6)]
    monkeypatch.setattr(ats, "load_employers", lambda: employers)

    def reader(_client, employer):
        if employer["slug"] == "w2":
            raise TypeError("a maintenance page where JSON was expected")
        return [{"url": f"https://jobs.test/{employer['slug']}", "title": "Planner", "location": "Utrecht", "description": FULL, "posted_at": ""}]

    monkeypatch.setitem(ats.SYSTEMS, "lever", reader)
    entry = {"status": "ok", "new": 0, "updated": 0, "items": 0}
    ats._scan_ats(object(), {"id": "x"}, entry, {"new": 0, "updated": 0})
    assert entry["new"] == 5 and "w2" in entry["reason"]  # the boards after it were still read
    with store.connect() as con:
        assert con.execute("SELECT last_status FROM sources WHERE id='x'").fetchone()[0] == "ok"


def test_eures_reads_what_it_found_when_a_late_search_page_fails(client):  # noqa: F811
    store.sync_sources([{"id": "eures", "kind": "eures-json"}])

    class Flaky(FakeEures):
        def post_json(self, url, payload):
            self.searched.append(payload)
            request = httpx.Request("POST", url)
            if payload["page"] == 2:
                return httpx.Response(503, request=request)
            return httpx.Response(200, json={"jvs": self.items}, request=request)

    fake = Flaky([{"id": "a"}], {"a": _detail("Beleidsadviseur", "Utrecht")})
    entry = {"items": 0, "new": 0, "updated": 0}
    eures._scan_eures(fake, {"id": "eures", "pages": 5}, entry, {"new": 0, "updated": 0})
    assert entry["new"] == 1 and "pagina 2" in entry["reason"]


def test_travel_is_refreshed_in_pieces_and_then_left_alone(client, monkeypatch):  # noqa: F811
    profile.write_preferences({"home": {"label": "Amsterdam", "lat": 52.3676, "lon": 4.9041}})
    for index in range(5):
        add_job(index, location="Utrecht")
    assert signals.refresh_travel(limit=2) == 2  # a page takes a piece
    assert signals.refresh_travel() == 3  # the worker the rest
    with monkeypatch.context() as patch:
        patch.setattr(store, "jobs_missing_travel", lambda *_: pytest.fail("looked up again"))
        assert signals.refresh_travel() == 0
    add_job(9, location="Zwolle")  # a new vacancy makes it look again
    assert signals.refresh_travel() == 1


def test_old_finished_work_is_cleared_and_waiting_work_is_not():
    done = tasks.enqueue("rerank", owner="a", dedupe_key="rerank:a")
    tasks.claim_next()
    tasks.finish(done, {})
    waiting = tasks.enqueue("match", owner="a", dedupe_key="match:a")
    with store.connect() as con:
        con.execute("UPDATE tasks SET finished_at='2020-01-01T00:00:00+00:00', created_at='2020-01-01T00:00:00+00:00'")
    assert tasks.prune() == 1
    assert tasks.get(waiting, owner="a")["status"] == "queued" and tasks.get(done, owner="a") is None
    store.record_detail_fetch("https://example.test/old")
    with store.connect() as con:
        con.execute("UPDATE meta SET value='2020-01-01T00:00:00+00:00' WHERE key LIKE 'detail_fetched:%'")
    store.record_detail_fetch("https://example.test/new")
    assert store.prune_detail_fetches() == 1 and store.fetched_within("https://example.test/new", 60)
