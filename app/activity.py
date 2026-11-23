"""What each account did in the app and when: which screens were opened, which actions taken.

One JSON line per page view or action in `<workspace>/activity/YYYY-MM-DD.jsonl`, next to
the AI log, so an account export and deletion include it. Administrators read it to see
whether people use the app and which parts, and to find what to improve.

Only the kind of action is kept: the route as written in the code (`/jobs/{job_id}/vote`),
never the address that was asked for, a search term, a form value or which vacancy it was.
Files older than `ACTIVITY_LOG_DAYS` are removed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import config, tenancy

log = logging.getLogger("jobcraft")

DEFAULT_RETENTION_DAYS = 90
REPEAT_SECONDS = 90  # a page that reloads itself while it waits is one view, not hundreds
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

# (method, route) -> (part of the app, what the person did). Anything not listed is still
# logged, under "Overig" with its route, so a new screen never goes unseen.
ACTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("GET", "/"): ("Vandaag", "Vandaag bekeken"),
    ("POST", "/vandaag/oordeel/{job_id}"): ("Vandaag", "Vijf voor vandaag beantwoord"),
    ("POST", "/jobs/{job_id}/reden"): ("Vandaag", "Reden gegeven bij een nee"),
    ("GET", "/jobs"): ("Vacatures", "Vacaturelijst bekeken"),
    ("GET", "/jobs/nieuw"): ("Vacatures", "Nieuwe matches bekeken"),
    ("GET", "/jobs/doorlopen"): ("Vacatures", "Eén voor één bekeken"),
    ("GET", "/jobs/{job_id}"): ("Vacatures", "Vacature geopend"),
    ("GET", "/jobs/{job_id}/tekst"): ("Vacatures", "Vacaturetekst geladen"),
    ("GET", "/jobs/{job_id}/oordeel"): ("Vacatures", "Beoordeling geladen"),
    ("POST", "/jobs/{job_id}/vote"): ("Vacatures", "Duim gegeven"),
    ("POST", "/jobs/{job_id}/status"): ("Vacatures", "Bewaard, genegeerd of gereageerd"),
    ("POST", "/jobs/match"): ("Vacatures", "Beoordeling gestart"),
    ("POST", "/jobs/reisbereik"): ("Vacatures", "Reisbereik aangepast"),
    ("POST", "/jobs/{job_id}/oordeel/dieper"): ("Vacatures", "Uitgebreide beoordeling gevraagd"),
    ("POST", "/jobs/{job_id}/inzicht/{kind}"): ("Vacatures", "Vergelijking of gespreksvoorbereiding gevraagd"),
    ("GET", "/jobs/toevoegen"): ("Vacatures", "Vacature toevoegen geopend"),
    ("POST", "/jobs/toevoegen"): ("Vacatures", "Vacature toegevoegd"),
    ("GET", "/sollicitaties"): ("Sollicitaties", "Sollicitaties bekeken"),
    ("POST", "/sollicitaties/{job_id}"): ("Sollicitaties", "Sollicitatie bijgewerkt"),
    ("GET", "/jobs/{job_id}/sollicitatie"): ("Documenten", "Sollicitatie geopend"),
    ("POST", "/jobs/{job_id}/sollicitatie/plan"): ("Documenten", "Sollicitatieplan gevraagd"),
    ("POST", "/jobs/{job_id}/sollicitatie/schrijven"): ("Documenten", "Cv of brief laten schrijven"),
    ("POST", "/jobs/{job_id}/documents"): ("Documenten", "Cv of brief laten schrijven"),
    ("POST", "/jobs/{job_id}/sollicitatie/documenten/{document_id}/toon"): ("Documenten", "Andere toon gevraagd"),
    ("POST", "/jobs/{job_id}/sollicitatie/documenten/{document_id}/tekst"): ("Documenten", "Tekst aangepast"),
    ("POST", "/jobs/{job_id}/sollicitatie/documenten/{document_id}/opmaak"): ("Documenten", "Opmaak gekozen"),
    ("POST", "/jobs/{job_id}/sollicitatie/gegevens"): ("Documenten", "Contactgegevens opgeslagen"),
    ("POST", "/jobs/{job_id}/sollicitatie/gegevens/vernieuwen"): ("Documenten", "Documenten vernieuwd met nieuwe gegevens"),
    ("GET", "/jobs/{job_id}/documents/{document_id}/{fmt}"): ("Documenten", "Document gedownload"),
    ("GET", "/jobs/{job_id}/documents/{document_id}/verschil"): ("Documenten", "Versies vergeleken"),
    ("GET", "/jobs/{job_id}/documents/{document_id}/bewerken"): ("Documenten", "Document bewerken geopend"),
    ("POST", "/jobs/{job_id}/documents/{document_id}/bewerken"): ("Documenten", "Document bewerkt"),
    ("POST", "/jobs/{job_id}/documents/{document_id}/herschrijven"): ("Documenten", "Document laten herschrijven"),
    ("POST", "/jobs/{job_id}/documents/{document_id}/verwijderen"): ("Documenten", "Document verwijderd"),
    ("GET", "/profile"): ("Profiel", "Profiel bekeken"),
    ("POST", "/profile/cv/inlezen"): ("Profiel", "Cv ingelezen"),
    ("POST", "/profile/cv/coach"): ("Profiel", "Cv-coach gevraagd"),
    ("POST", "/profile/zoek"): ("Profiel", "Zoekwensen opgeslagen"),
    ("POST", "/profile/home"): ("Profiel", "Woonplaats of reisbereik opgeslagen"),
    ("POST", "/profile/consolidate"): ("Profiel", "Profiel laten bijwerken"),
    ("GET", "/account"): ("Account", "Account bekeken"),
    ("POST", "/account/password"): ("Account", "Wachtwoord gewijzigd"),
    ("POST", "/account/dagmail"): ("Account", "Dagmail ingesteld"),
    ("POST", "/account/export"): ("Account", "Gegevens gedownload"),
    ("GET", "/sources"): ("Bronnen", "Bronnen bekeken"),
    ("POST", "/sources/{source_id}/preference"): ("Bronnen", "Bron aan- of uitgezet"),
}
PREFIX_PARTS = (
    ("/start", "Snelle start"),
    ("/profile/cv", "Profiel"),
    ("/profile", "Profiel"),
    ("/admin", "Beheer"),
    ("/users", "Beheer"),
    ("/sources", "Beheer"),
    ("/account", "Account"),
)
# Not something a person did: assets, health checks, background polls and preview images.
_SKIPPED = re.compile(r"^/(?:static/|healthz$|api/|login$|logout$|uitnodiging/)|/voorbeeld/")

_last: dict[str, tuple[str, float]] = {}
_guard = threading.Lock()


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get("ACTIVITY_LOG_DAYS", DEFAULT_RETENTION_DAYS)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def _directory(owner: str) -> Path:
    if owner == tenancy.LEGACY_ID:
        return config.DATA_DIR / "activity"
    return config.DATA_DIR / "users" / owner / "activity"


def describe(method: str, route: str) -> tuple[str, str]:
    """(part of the app, what was done) for a route as the code names it."""
    known = ACTIONS.get((method, route))
    if known:
        return known
    part = next((name for prefix, name in PREFIX_PARTS if route.startswith(prefix)), "Overig")
    return part, ("Geopend: " if method == "GET" else "Actie: ") + route


def _repeat(owner: str, method: str, path: str) -> bool:
    """The same page asked for again within a moment: a wait that reloads itself, or a drawer
    that checks for news. As long as it keeps coming it stays one view."""
    if method != "GET":
        return False
    now = time.monotonic()
    with _guard:
        previous = _last.get(owner)
        _last[owner] = (path, now)
        if len(_last) > 2000:  # accounts come and go; this is no place to grow
            _last.clear()
    return previous is not None and previous[0] == path and now - previous[1] < REPEAT_SECONDS


def record(owner: str, method: str, route: str, path: str, status: int, duration_ms: int) -> None:
    """Never let bookkeeping break the request it describes."""
    try:
        if method not in ("GET", "POST") or _SKIPPED.search(route) or status in (401, 403, 404, 405):
            return
        if _repeat(owner, method, path):
            return
        part, action = describe(method, route)
        now = datetime.now(UTC)
        directory = _directory(owner)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{now:%Y-%m-%d}.jsonl"
        new_day = not target.exists()
        line = json.dumps(
            {"at": now.isoformat(), "part": part, "action": action, "method": method, "route": route,
             "status": status, "ms": duration_ms},
            ensure_ascii=False,
        )
        # One write on an append-only file: the web and worker processes cannot interleave.
        descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (line + "\n").encode())
        finally:
            os.close(descriptor)
        if new_day:
            cutoff = (now - timedelta(days=retention_days())).strftime("%Y-%m-%d")
            for old in directory.glob("*.jsonl"):
                if old.stem < cutoff:
                    old.unlink(missing_ok=True)
    except Exception:
        log.exception("could not write the activity log")


def _rows(owner: str, since: str) -> list[dict]:
    found = []
    for path in sorted(_directory(owner).glob("*.jsonl"), reverse=True):
        if not _DAY.fullmatch(path.stem) or path.stem < since[:10]:
            continue
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for raw in reversed(lines):
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if str(row.get("at") or "") >= since:
                found.append({**row, "owner": owner})
    return found


def recent(owners: list[str], days: int, *, part: str = "", limit: int = 500) -> tuple[list[dict], bool]:
    """Newest first over the given accounts; the flag says whether the limit cut rows off."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = [row for owner in owners for row in _rows(owner, since) if not part or row.get("part") == part]
    rows.sort(key=lambda row: row["at"], reverse=True)
    return rows[:limit], len(rows) > limit


def summary(owners: list[str], days: int) -> list[dict]:
    """Per account: when last active, on how many days, and how often each part was used."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    result = []
    for owner in owners:
        rows = _rows(owner, since)
        parts: dict[str, int] = {}
        for row in rows:
            parts[row.get("part") or "Overig"] = parts.get(row.get("part") or "Overig", 0) + 1
        result.append({
            "owner": owner,
            "last": rows[0]["at"] if rows else "",
            "days": len({row["at"][:10] for row in rows}),
            "actions": sum(1 for row in rows if row.get("method") == "POST"),
            "views": sum(1 for row in rows if row.get("method") == "GET"),
            "parts": sorted(parts.items(), key=lambda item: -item[1]),
        })
    return sorted(result, key=lambda item: item["last"], reverse=True)
