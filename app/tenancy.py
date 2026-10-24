"""Request-local ownership, also explicitly captured for background tasks."""

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from types import MappingProxyType

_principal = ContextVar("principal", default=None)
LEGACY_ID = "legacy"


def current():
    return _principal.get()


def user_id() -> str:
    # Unscoped access is legacy-only for startup/migrations and existing local
    # maintenance scripts. HTTP handlers must run inside auth.middleware's scope;
    # every spawned user worker must use capture(). Never switch module globals.
    user = current()
    return user["id"] if user else LEGACY_ID


def display_name() -> str:
    user = current()
    return (user.get("display_name") or user["username"]) if user else "Owner"


@contextmanager
def scope(user):
    """Scope an owner explicitly; None clears ownership for shared work."""
    token = _principal.set(MappingProxyType(dict(user)) if user is not None else None)
    try:
        yield
    finally:
        _principal.reset(token)


def capture(function):
    """Freeze the caller's ownership for a newly spawned worker thread."""
    context = copy_context()
    return lambda *args, **kwargs: context.run(function, *args, **kwargs)
