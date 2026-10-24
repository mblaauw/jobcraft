"""local json source adapter."""
from __future__ import annotations

import json

from .. import config, store
from .common import _tally, make_job


def _scan_local_json(client, source, entry, summary, limit_per_source=None) -> None:
    """Read pre-fetched vacancies (browser import or manual) from data/imports/*.json."""
    relative = source.get("file") or f"imports/{source['id']}.json"
    path = config.DATA_DIR / relative
    if not path.exists():
        raise FileNotFoundError(
            f"{path} ontbreekt; draai eerst scripts/browser_import.py (of vul het bestand met de hand)"
        )
    payload = json.loads(path.read_text())
    items = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError('importbestand moet een lijst of {"jobs": [...]} zijn')
    limit = limit_per_source or int(source.get("per_page", 100))
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("link")
        title = item.get("title")
        if not url or not title:
            continue
        job = make_job(
            source["id"],
            url,
            str(title),
            company=item.get("company") or item.get("employer") or "",
            location=item.get("location") or "",
            hours=item.get("hours") or "",
            description=item.get("description") or "",
            posted_at=item.get("posted_at") or "",
            lat=item.get("lat"),
            lon=item.get("lon"),
        )
        _, created = store.upsert_job(job)
        _tally(entry, summary, created)
    store.update_source_status(source["id"], "ok")



scan = _scan_local_json


KINDS = ('local-json',)
