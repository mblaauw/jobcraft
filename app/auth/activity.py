"""Record sign-in activity and say in plain words where a request came from.

The address is what uvicorn reports as the client. Behind a proxy that is the proxy's
address unless uvicorn trusts the forwarded headers, which the deployment sets through
``FORWARDED_ALLOW_IPS``; nothing here parses those headers itself.
"""

from __future__ import annotations

from fastapi import Request

from .. import store

_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser/", "Samsung Internet"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("CriOS/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Mac OS X", "Mac"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


def client_address(request: Request) -> str:
    return request.client.host if request.client else ""


def browser_label(user_agent: str | None) -> str:
    """"Safari op iPhone" from a user agent string; "onbekende browser" when it says nothing."""
    agent = user_agent or ""
    browser = next((name for marker, name in _BROWSERS if marker in agent), "")
    system = next((name for marker, name in _SYSTEMS if marker in agent), "")
    if not browser and not system:
        return "onbekende browser"
    if browser and system:
        return f"{browser} op {system}"
    return browser or system


def record(
    request: Request,
    kind: str,
    *,
    user_id: str | None = None,
    username: str | None = None,
    actor_id: str | None = None,
) -> None:
    """Never lets bookkeeping break a sign-in: a failed insert is logged and swallowed."""
    try:
        store.record_auth_event(
            kind,
            user_id=user_id,
            username=username,
            ip=client_address(request),
            user_agent=request.headers.get("user-agent"),
            actor_id=actor_id,
        )
    except Exception:
        from .constants import log

        log.exception("auth event not recorded")
