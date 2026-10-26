"""One shared renderer; lazy CSRF binding avoids authentication import cycles."""

from datetime import UTC, datetime

from fastapi.templating import Jinja2Templates

from . import config
from .util import LOCAL_TZ

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


def _amsterdam(value):
    """Convert a UTC ISO timestamp to the account's local Europe/Amsterdam time."""
    if not value:
        return value
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(LOCAL_TZ).isoformat()
    except ValueError:
        return value


def _csrf_input(request):
    from .auth import csrf_input

    return csrf_input(request)


templates.env.globals["csrf_input"] = _csrf_input
templates.env.filters["amsterdam"] = _amsterdam
