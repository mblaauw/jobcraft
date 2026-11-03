"""Reviewed method/path inventory: new endpoints require an explicit policy."""

from fastapi.routing import APIRoute

from app import auth
from app.routers import (
    admin_ai,
    cv,
    interview,
    jobs,
    pages,
    profile,
    sources,
    start,
    studio,
    tasks,
    workspace,
)

PUBLIC = {
    ("GET", "/login"),
    ("POST", "/login"),
    ("GET", "/healthz"),
    ("GET", "/uitnodiging/{token}"),
    ("POST", "/uitnodiging/{token}"),
}
ADMIN = {
    ("GET", "/admin"),
    ("GET", "/admin/ai"),
    ("POST", "/admin/ai/modellen"),
    ("POST", "/admin/ai/test"),
    ("GET", "/admin/ai/log/{owner}/{day}/{offset}"),
    ("GET", "/users"),
    ("POST", "/users"),
    ("POST", "/users/uitnodiging"),
    ("POST", "/users/{user_id}/deactivate"),
    ("POST", "/users/{user_id}/activate"),
    ("POST", "/users/{user_id}/reset-password"),
    ("POST", "/sources/scan"),
    ("POST", "/sources/schedule"),
    ("POST", "/sources/{source_id}/toggle"),
}
USER = {
    ("POST", "/logout"),
    ("GET", "/api/werk"),
    ("GET", "/jobs/{job_id}/tekst"),
    ("GET", "/jobs/{job_id}/oordeel"),
    ("POST", "/profile/positie"),
    ("GET", "/jobs/{job_id}/sollicitatie"),
    ("POST", "/jobs/{job_id}/sollicitatie/plan"),
    ("POST", "/jobs/{job_id}/sollicitatie/schrijven"),
    ("POST", "/jobs/{job_id}/sollicitatie/gegevens"),
    ("POST", "/jobs/{job_id}/sollicitatie/verhaal/{index}/verwijderen"),
    ("POST", "/jobs/{job_id}/sollicitatie/documenten/{document_id}/opmaak"),
    ("POST", "/jobs/{job_id}/sollicitatie/documenten/{document_id}/tekst"),
    ("POST", "/jobs/{job_id}/sollicitatie/documenten/{document_id}/toon"),
    ("GET", "/jobs/{job_id}/sollicitatie/documenten/{document_id}/voorbeeld/{page}.png"),
    ("POST", "/jobs/{job_id}/documents/{document_id}/herschrijven"),
    ("GET", "/jobs/{job_id}/documents/{document_id}/verschil"),
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
    ("GET", "/start"),
    ("GET", "/start/cv"),
    ("POST", "/start/cv"),
    ("POST", "/start/cv/confirm"),
    ("GET", "/start/keuzes"),
    ("POST", "/start/keuzes"),
    ("POST", "/start/keuzes/teller"),
    ("GET", "/start/verder"),
    ("POST", "/start/zelf"),
    ("POST", "/start/vacatures/stoppen"),
    ("GET", "/start/vacatures"),
    ("POST", "/start/vacatures/{job_id}"),
    ("GET", "/start/vragen"),
    ("POST", "/start/vragen/volgende"),
    ("POST", "/start/vragen/antwoord"),
    ("POST", "/start/klaar"),
    ("GET", "/sollicitaties"),
    ("POST", "/sollicitaties/{job_id}"),
    ("GET", "/jobs/doorlopen"),
    ("GET", "/jobs/nieuw"),
    ("GET", "/jobs/toevoegen"),
    ("POST", "/jobs/toevoegen"),
    ("POST", "/jobs/{job_id}/inzicht/{kind}"),
    ("GET", "/jobs/{job_id}/documents/{document_id}/bewerken"),
    ("POST", "/jobs/{job_id}/documents/{document_id}/bewerken"),
    ("POST", "/account/export"),
    ("POST", "/account/verwijderen"),
    ("POST", "/account/dagmail"),
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
    ("POST", "/profile/sectie"),
    ("POST", "/profile/zoek"),
    ("POST", "/profile/notities"),
    ("POST", "/profile/cv/inlezen"),
    ("POST", "/profile/cv/omzetting"),
    ("POST", "/profile/cv/over-mij"),
    ("POST", "/profile/cv/functie"),
    ("POST", "/profile/cv/functie/{role_id}/{actie}"),
    ("POST", "/profile/cv/opleiding"),
    ("POST", "/profile/cv/lijst/{key}"),
    ("POST", "/profile/cv/coach"),
    ("POST", "/profile/cv/coach/{note_id}"),
    ("POST", "/profile/aantekeningen-apart"),
    ("POST", "/profile/onderdelen-terug"),
    ("POST", "/profile/restore/{version_id}"),
    ("GET", "/api/tasks"),
    ("POST", "/jobs/{job_id}/documents/{document_id}/verwijderen"),
    ("GET", "/api/tasks/{task_id}"),
}


def test_every_endpoint_has_reviewed_permission_dependency():
    groups = (
        auth,
        admin_ai,
        cv,
        interview,
        jobs,
        pages,
        profile,
        sources,
        start,
        studio,
        tasks,
        workspace,
    )
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
