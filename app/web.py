"""One shared renderer; lazy CSRF binding avoids authentication import cycles."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from . import config

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))

_AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _amsterdam(value):
    """Convert a UTC ISO timestamp to Europe/Amsterdam local time."""
    if not value:
        return value
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(_AMSTERDAM).isoformat()
    except ValueError:
        return value


def _csrf_input(request):
    from .auth import csrf_input

    return csrf_input(request)


templates.env.globals["csrf_input"] = _csrf_input
templates.env.filters["amsterdam"] = _amsterdam
