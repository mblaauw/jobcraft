"""Job-source adapters and the scan runner.

Adapters (see `seeds/sources.yaml` for the verified configuration):

- ``wp-posts`` / ``wp-job-listings``  WordPress REST (title, date, link, content)
- ``html-fondsen``                    fondsen.org HTML list + detail pages
- ``html-listing``                    generic list -> detail pages, configurable
                                      with `link_pattern`, `pages`, optional
                                      `location_from_slug`
- ``sitemap-jsonld``                  sitemap -> detail pages with schema.org
                                      JobPosting JSON-LD (pagination-free)
- ``rss``                             RSS/Atom feed
- ``exenzo-json``                     Exenzo jobboard API (Greenjobs)

Every adapter normalises to the same job dict and calls ``store.upsert_job``.
Polite scanning: sequential requests with a small delay per source.
"""

from __future__ import annotations

import re
import shutil
import time

import httpx
import yaml

from . import config, source_policy, store
from .adapters import registry
from .adapters.common import _tally, make_job, page_text, split_company, strip_html  # noqa: F401
from .adapters.defensie import (  # noqa: F401
    _defensie_component_payloads,
    _defensie_details,
    _scan_defensie,
    normalize_defensie,
)
from .adapters.exenzo import _scan_exenzo, normalize_exenzo  # noqa: F401
from .adapters.fondsen import _scan_fondsen  # noqa: F401
from .adapters.html_listing import _scan_html_listing  # noqa: F401
from .adapters.jsonld import _scan_sitemap_jsonld, jsonld_company, jsonld_jobs, jsonld_location  # noqa: F401
from .adapters.local_json import _scan_local_json  # noqa: F401
from .adapters.rijksoverheid import (  # noqa: F401
    _hn_ref,
    _parse_hn_cards,
    _rijksoverheid_detail,
    _scan_rijksoverheid,
)
from .adapters.rss import _scan_rss  # noqa: F401
from .adapters.wordpress import normalize_wp_item  # noqa: F401
from .util import now_iso

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SEED_FILE = config.BASE_DIR / "seeds" / "sources.yaml"
DATA_FILE = config.DATA_DIR / "sources.yaml"


def ensure_sources_file() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists() and SEED_FILE.exists():
        shutil.copy2(SEED_FILE, DATA_FILE)


def load_sources() -> list[dict]:
    configured = store.load_source_configs()
    if configured:
        return [source_policy.effective_metadata(source) for source in configured]
    ensure_sources_file()
    if not DATA_FILE.exists():
        return []
    payload = yaml.safe_load(DATA_FILE.read_text()) or {}
    sources = payload.get("sources") or []
    result = [source for source in sources if isinstance(source, dict) and source.get("id")]
    for source in result:
        store.save_source_config(source)
    return [source_policy.effective_metadata(source) for source in result]


def set_enabled(source_id: str, enabled: bool) -> bool:
    """Flip one source's `enabled` flag in data/sources.yaml, keeping the file's comments."""
    ensure_sources_file()
    text = DATA_FILE.read_text()
    block = re.compile(
        rf"(^[ \t]*-[ \t]*id:[ \t]*{re.escape(source_id)}[ \t]*\n)(.*?)(?=^[ \t]*-[ \t]*id:|\Z)",
        re.M | re.S,
    )
    match = block.search(text)
    if not match:
        return store.set_source_config_enabled(source_id, enabled)
    body = match.group(2)
    desired = "true" if enabled else "false"
    if re.search(r"^[ \t]*enabled:[ \t]*(true|false)[ \t]*$", body, re.M):
        new_body = re.sub(
            r"^([ \t]*enabled:[ \t]*)(true|false)([ \t]*)$",
            lambda m: f"{m.group(1)}{desired}{m.group(3)}",
            body,
            count=1,
            flags=re.M,
        )
    else:
        new_body = body.rstrip("\n") + f"\n    enabled: {desired}\n"
    DATA_FILE.write_text(text[: match.start(2)] + new_body + text[match.end(2) :])
    store.set_source_config_enabled(source_id, enabled)
    return True












# ---------------------------------------------------------------- WordPress




# ---------------------------------------------------------------- fondsen.org




# ---------------------------------------------------------------- JSON-LD










# ---------------------------------------------------------------- html-listing




# ---------------------------------------------------------------- RSS / Atom




# ---------------------------------------------------------------- Exenzo API






# ---------------------------------------------------------------- Defensie


DEFENSIE_HOST = "https://werkenbijdefensie.nl"










# ---------------------------------------------------------------- Rijksoverheid










# ---------------------------------------------------------------- local-json import




# ---------------------------------------------------------------- runner


def scan(limit_per_source: int | None = None) -> dict:
    sources = [source for source in load_sources() if source.get("enabled", True)]
    summary: dict = {
        "started_at": now_iso(),
        "finished_at": None,
        "sources": {},
        "new": 0,
        "updated": 0,
        "errors": [],
    }
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30
    ) as client:
        for source in sources:
            kind = source.get("kind")
            entry: dict = {"status": "ok", "new": 0, "updated": 0, "items": 0}
            try:
                adapter = registry().get(kind)
                if adapter is not None:
                    adapter(client, source, entry, summary, limit_per_source)
                else:
                    entry["status"] = "skipped"
                    entry["reason"] = f"adapter '{kind}' not implemented yet"
                    summary["sources"][source["id"]] = entry
                    continue
            except Exception as exc:  # noqa: BLE001 - surface per-source failures
                entry["status"] = "error"
                entry["reason"] = f"{type(exc).__name__}: {exc}"[:300]
                summary["errors"].append({"source": source["id"], "reason": entry["reason"]})
                store.update_source_status(source["id"], "error")
            summary["sources"][source["id"]] = entry
            time.sleep(0.5)
    summary["finished_at"] = now_iso()
    return summary
