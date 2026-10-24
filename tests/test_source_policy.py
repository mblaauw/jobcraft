from app import profile, source_policy, sources, store, tenancy
from tests.factories import add_job


def test_regional_defaults_overrides_and_no_home():
    regional = {"id": "region", "scope": "regional", "regions": ["Amsterdam"]}
    near = {"lat": 52.3676, "lon": 4.9041}
    far = {"lat": 51.4416, "lon": 5.4697}
    assert source_policy.selection(regional, near)["selected"]
    assert not source_policy.selection(regional, far)["selected"]
    assert source_policy.selection(regional, {})["selected"]
    assert source_policy.selection(regional, far, True)["selected"]
    assert not source_policy.selection(regional, near, False)["selected"]
    assert source_policy.selection({"id": "national"}, far)["selected"]


def test_regional_boundary_is_inclusive(monkeypatch):
    regional = {"id": "region", "scope": "regional", "regions": ["Amsterdam"]}
    for distance, expected in [(39.9, True), (40, True), (40.1, False)]:
        monkeypatch.setattr(
            source_policy.geo, "haversine_km", lambda *args, distance=distance: distance
        )
        assert source_policy.selection(regional, {"lat": 52, "lon": 5})["selected"] is expected


def test_legacy_sql_metadata_fallback_does_not_overwrite_config():
    original = {"id": "greenjobs", "kind": "local-json", "file": "custom.json", "enabled": False}
    store.save_source_config(original)
    loaded = sources.load_sources()[0]
    assert loaded["scope"] == "regional" and loaded["regions"] == ["Amsterdam"]
    assert loaded["file"] == "custom.json" and loaded["enabled"] is False
    assert store.load_source_configs()[0] == original
    assert (
        source_policy.effective_metadata({**original, "scope": "national", "regions": []})["scope"]
        == "national"
    )


def test_one_override_does_not_whitelist_the_rest_and_owners_are_isolated():
    ids = [add_job(i) for i in range(3)]
    with store.connect() as con:
        for job_id, source in zip(ids, ["greenjobs", "national", "other"], strict=True):
            con.execute("UPDATE jobs SET source_id=? WHERE id=?", (source, job_id))
    with tenancy.scope({"id": "a", "username": "a"}):
        profile.write_preferences({"home": {"label": "Eindhoven", "lat": 51.4416, "lon": 5.4697}})
        store.set_user_source("other", False)
        assert {job["id"] for job in store.list_jobs()} == {ids[1]}
        assert {job["id"] for job in store.list_jobs_for_matching()} == {ids[1]}
        assert not store.effective_user_sources()["greenjobs"]["selected"]
        store.set_user_source("greenjobs", True)
        assert {job["id"] for job in store.list_jobs()} == {ids[0], ids[1]}
    with tenancy.scope({"id": "b", "username": "b"}):
        assert {job["id"] for job in store.list_jobs()} == set(ids)
