from __future__ import annotations

from app import config, tenancy, workspace


def test_workspace_keeps_legacy_paths_and_scopes_new_accounts():
    legacy = workspace.for_owner(tenancy.LEGACY_ID)
    assert legacy.profile_dir == config.DATA_DIR / "profile"
    assert legacy.jobs_dir == config.DATA_DIR / "jobs"
    assert legacy.templates_dir == config.DATA_DIR / "templates"

    member = workspace.for_owner("member-id")
    assert member.root == config.DATA_DIR / "users" / "member-id"
    assert member.profile_dir == member.root / "profile"
    assert member.jobs_dir == member.root / "jobs"
    assert member.templates_dir == member.root / "templates"


def test_current_workspace_follows_the_tenant_scope():
    with tenancy.scope({"id": "member-id", "username": "member"}):
        assert workspace.current().root.name == "member-id"
    assert workspace.current().root == config.DATA_DIR
