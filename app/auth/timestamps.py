"""Local authentication: timestamps."""
from __future__ import annotations

from datetime import UTC, datetime

from ..util import LOCAL_TZ


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()



def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)



def _label_ts(value: str | None) -> str:
    parsed = _parse_ts(value)
    return parsed.astimezone(LOCAL_TZ).strftime("%d-%m-%Y %H:%M") if parsed else ""



def _expired(value: str | None) -> bool:
    parsed = _parse_ts(value)
    return parsed is None or parsed <= datetime.now(UTC)

