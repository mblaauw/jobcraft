#!/usr/bin/env python
"""Browser-import voor vacaturebronnen die niet via platte HTTP te lezen zijn.

Gebruik:
    uv run --with playwright playwright install chromium      # eenmalig
    uv run --with playwright python scripts/browser_import.py <target> [--limit N]

Schrijft `data/imports/<target>.json` in het formaat dat de `local-json` adapter
leest:

    {"source": "<target>", "fetched_at": "...", "jobs": [{url, title, company,
      location, hours, description}]}

De app zelf draait nooit een browser: de container leest alleen het JSON-bestand.
Draai dit script dus waar een browser mag draaien (nu je Mac, later eventueel een
aparte container of cron-taak) en laat `data/` meelopen.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMPORT_DIR = ROOT / "data" / "imports"

# Per doel: lijstpagina, selectors en optionele detailstap. Vul selectors aan na
# een eerste inspectie; de kaarttekst wordt als titel gebruikt tenzij er een
# title_selector staat.
TARGETS: dict[str, dict] = {
    "politie": {
        "listing_url": "https://www.werkenbijdepolitie.nl/vacatures",
        "card_selector": "a[href*='/vacatures/']",
        "title_selector": None,
        "detail_text_selector": "main",
        "max_items": 25,
        "notes": "De site blokkeert niet-browser clients (WAF); selectors controleren bij de eerste run.",
    },
    "ats-platform": {
        "listing_url": "https://werkenbij.zaanstad.nl/vacatures/",
        "card_selector": "a[href*='ats-platform.com'], a[href*='/vacature']",
        "title_selector": None,
        "detail_text_selector": "main, .vacancy, body",
        "max_items": 25,
        "notes": "Gedeeld ATS; de kaartlinks kunnen de client-id prijsgeven. Selectors aanvullen na inspectie.",
    },
}


def load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - environment dependent
        print(
            "Playwright ontbreekt. Installeer het met:\n"
            "  uv run --with playwright playwright install chromium\n"
            "en draai daarna: uv run --with playwright python scripts/browser_import.py <target>",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    return sync_playwright


def scrape(target: str, limit: int) -> dict:
    config = TARGETS[target]
    sync_playwright = load_playwright()
    jobs: list[dict] = []
    origin = config["listing_url"].split("/vacatures")[0]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="nl-NL")
        page.goto(config["listing_url"], wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        cards = page.query_selector_all(config["card_selector"])
        seen: set[str] = set()
        for card in cards:
            href = card.get_attribute("href") or card.get_attribute("data-href") or ""
            if not href or href.startswith("#"):
                continue
            url = (
                href
                if href.startswith("http")
                else origin + ("" if href.startswith("/") else "/") + href
            )
            if url in seen:
                continue
            seen.add(url)
            text = " ".join((card.inner_text() or "").split())
            title = ""
            if config.get("title_selector"):
                node = card.query_selector(config["title_selector"])
                if node is not None:
                    title = " ".join((node.inner_text() or "").split())
            item = {
                "url": url,
                "title": (title or text)[:160],
                "company": "",
                "location": "",
                "hours": "",
                "description": "",
            }
            selector = config.get("detail_text_selector")
            if selector:
                try:
                    detail = browser.new_page(locale="nl-NL")
                    detail.goto(url, wait_until="domcontentloaded", timeout=45000)
                    detail.wait_for_timeout(1200)
                    node = detail.query_selector(selector)
                    if node is not None:
                        item["description"] = " ".join((node.inner_text() or "").split())[:20000]
                    detail.close()
                except Exception as exc:  # pragma: no cover - network reality
                    print(f"  detail mislukt voor {url}: {type(exc).__name__}", file=sys.stderr)
            jobs.append(item)
            if len(jobs) >= limit:
                break
        browser.close()
    return {
        "source": target,
        "fetched_at": datetime.now(UTC).isoformat(),
        "listing_url": config["listing_url"],
        "jobs": jobs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Browser-import naar data/imports/<target>.json")
    parser.add_argument("target", choices=sorted(TARGETS))
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = scrape(args.target, args.limit)
    out = IMPORT_DIR / f"{args.target}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"{len(payload['jobs'])} vacatures geschreven naar {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
