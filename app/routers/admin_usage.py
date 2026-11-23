"""Admin: which parts of the app each account uses, and when."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import activity, auth
from ..auth.users import _list_users
from ..util import LOCAL_TZ
from ..web import templates

router = APIRouter(dependencies=[Depends(auth.require_admin)])
PERIODS = {"dag": 1, "week": 7, "maand": 31}


def _when(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone(LOCAL_TZ).strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return ""


@router.get("/users/gebruik", response_class=HTMLResponse)
def usage_page(request: Request, account: str = "", onderdeel: str = "", periode: str = "week"):
    """Who used what and when. Kinds of actions only: no addresses, search terms or vacancies."""
    names = {row["id"]: row["display_name"] or row["username"] for row in _list_users()}
    account = account if account in names else ""
    period = periode if periode in PERIODS else "week"
    owners = [account] if account else list(names)
    events, cut_off = activity.recent(owners, PERIODS[period], part=onderdeel)
    summary = activity.summary(list(names), PERIODS[period])
    for row in (*events, *summary):
        row["when"] = _when(row.get("at") or row.get("last") or "")
    parts = sorted({part for item in summary for part, _count in item["parts"]})
    return templates.TemplateResponse(
        request,
        "users_usage.html",
        {
            "names": names,
            "events": events,
            "cut_off": cut_off,
            "summary": summary,
            "parts": parts,
            "periods": list(PERIODS),
            "filter_account": account,
            "filter_part": onderdeel if onderdeel in parts else "",
            "filter_period": period,
            "retention_days": activity.retention_days(),
        },
    )
