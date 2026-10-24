"""One shared renderer; lazy CSRF binding avoids authentication import cycles."""

from fastapi.templating import Jinja2Templates

from . import config

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


def _csrf_input(request):
    from .auth import csrf_input

    return csrf_input(request)


templates.env.globals["csrf_input"] = _csrf_input
