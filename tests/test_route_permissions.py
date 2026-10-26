"""Reviewed method/path inventory: new endpoints require an explicit policy."""

from fastapi.routing import APIRoute

from app import auth
from app.routers import account, interview, jobs, pages, profile, sources, tasks

PUBLIC = {("GET", "/login"), ("POST", "/login"), ("GET", "/healthz")}
ADMIN = {
    ("GET", "/admin"),
    ("GET", "/users"),
    ("POST", "/users"),
    ("POST", "/users/{user_id}/deactivate"),
    ("POST", "/users/{user_id}/activate"),
    ("POST", "/users/{user_id}/reset-password"),
    ("POST", "/sources/scan"),
    ("POST", "/sources/{source_id}/toggle"),
}
USER = {
    ("POST", "/logout"),
    ("GET", "/account"),
    ("POST", "/account/password"),
    ("GET", "/"),
    ("GET", "/interview"),
    ("POST", "/interview/toggle"),
    ("POST", "/interview/new"),
    ("GET", "/api/sessions"),
    ("GET", "/api/sessions/{session_id}/messages"),
    ("POST", "/api/sessions/{session_id}/send"),
    ("POST", "/api/sessions/{session_id}/send/stream"),
    ("GET", "/sources"),
    ("POST", "/sources/{source_id}/preference"),
    ("GET", "/jobs"),
    ("POST", "/jobs/match"),
    ("GET", "/jobs/{job_id}"),
    ("POST", "/jobs/{job_id}/documents"),
    ("GET", "/jobs/{job_id}/documents/{document_id}/{fmt}"),
    ("POST", "/jobs/{job_id}/vote"),
    ("POST", "/jobs/{job_id}/status"),
    ("GET", "/profile"),
    ("POST", "/profile/consolidate"),
    ("POST", "/profile/consolidate/accept"),
    ("POST", "/profile/consolidate/discard"),
    ("POST", "/profile/consolidate/save-draft"),
    ("POST", "/profile/home"),
    ("POST", "/profile/save"),
    ("POST", "/profile/restore/{version_id}"),
    ("POST", "/account/cv"),
    ("POST", "/account/cv/confirm"),
    ("POST", "/account/cv/cancel"),
    ("GET", "/api/tasks"),
    ("GET", "/api/tasks/{task_id}"),
}


def test_every_endpoint_has_reviewed_permission_dependency():
    groups = (auth, account, interview, jobs, pages, profile, sources, tasks)
    routes = [
        route for group in groups for route in group.router.routes if isinstance(route, APIRoute)
    ]
    inventory = {(method, route.path) for route in routes for method in route.methods}
    assert inventory == PUBLIC | ADMIN | USER
    assert not (PUBLIC & ADMIN or ADMIN & USER or PUBLIC & USER)
    for route in routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        for method in route.methods:
            key = method, route.path
            if key in PUBLIC:
                assert not dependencies & {auth.require_admin, auth.require_user}
            elif key in ADMIN:
                assert auth.require_admin in dependencies, key
            else:
                assert auth.require_user in dependencies, key
