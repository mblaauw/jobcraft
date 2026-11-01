"""Friendly error pages for browser navigation; API and script callers keep JSON."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.exceptions import HTTPException

from .web import templates

log = logging.getLogger("jobcraft")

HEADINGS = {
    400: "Dat lukte niet",
    401: "Log eerst in",
    403: "Hier heb je geen toegang toe",
    404: "Deze pagina bestaat niet",
    409: "Er is eerst iets anders nodig",
    413: "Dit bestand is te groot",
}
GENERIC = {
    404: "De vacature of pagina is er niet meer, of de link klopt niet.",
    500: "Er ging aan onze kant iets mis. Probeer het zo nog eens.",
}


def _wants_page(request: Request) -> bool:
    path = request.url.path
    if path == "/api" or path.startswith("/api/"):
        return False
    return "text/html" in request.headers.get("accept", "")


def _page(request: Request, status_code: int, detail: str | None, headers=None) -> HTMLResponse:
    # Messages written for people end in a full stop; bare internal codes never reach them.
    message = detail if detail and detail.endswith(".") and status_code != 404 else None
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "heading": HEADINGS.get(status_code, "Er ging iets mis"),
            "message": message or GENERIC.get(status_code, GENERIC[500]),
            "action_url": "/account" if status_code == 409 else "",
            "action_label": "Naar je account",
        },
        status_code=status_code,
        headers=headers,
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def friendly_http_error(request: Request, exc: HTTPException):
        if exc.status_code < 400 or not _wants_page(request):
            return await http_exception_handler(request, exc)
        return _page(request, exc.status_code, str(exc.detail), exc.headers)

    @app.exception_handler(Exception)
    async def friendly_crash(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path, exc_info=exc)
        if _wants_page(request):
            return _page(request, 500, None)
        return PlainTextResponse("Interne fout.", status_code=500)
