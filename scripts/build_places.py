"""Rebuild app/data/places_nl.csv from the public PDOK Locatieserver (BAG woonplaatsen).

Run by a maintainer, never by the app: places ship with the code, so a user's home
never leaves the machine. Hand-kept rows (rail flags, aliases, districts) always win.

    uv run python scripts/build_places.py
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import httpx

TARGET = Path(__file__).resolve().parent.parent / "app" / "data" / "places_nl.csv"
API = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
FIELDS = "woonplaatsnaam,centroide_ll,gemeentenaam,provincieafkorting"
PAGE = 100


def fetch() -> list[dict]:
    docs: list[dict] = []
    with httpx.Client(timeout=30) as client:
        while True:
            response = client.get(
                API,
                params={
                    "q": "*:*",
                    "fq": "type:woonplaats",
                    "rows": PAGE,
                    "start": len(docs),
                    "fl": FIELDS,
                    "sort": "woonplaatsnaam asc",
                },
            )
            response.raise_for_status()
            body = response.json()["response"]
            docs.extend(body["docs"])
            if len(docs) >= body["numFound"] or not body["docs"]:
                return docs


def main() -> None:
    with TARGET.open(newline="", encoding="utf-8") as handle:
        kept = list(csv.DictReader(handle))
    # A rebuild replaces earlier generated rows; only rows without a source stay hand-kept.
    kept = [row for row in kept if (row.get("origin") or "") != "pdok"]
    known = {row["name"].strip().lower() for row in kept if not (row.get("city") or "").strip()}

    groups: dict[str, list[dict]] = {}
    for doc in fetch():
        groups.setdefault(doc["woonplaatsnaam"].strip().lower(), []).append(doc)

    added: list[dict] = []

    def add(name: str, label: str, doc: dict) -> None:
        match = re.match(r"POINT\(([-\d.]+) ([-\d.]+)\)", doc["centroide_ll"])
        if not match or name in known:
            return
        known.add(name)
        added.append(
            {
                "name": name,
                "lat": f"{float(match.group(2)):.4f}",
                "lon": f"{float(match.group(1)):.4f}",
                "rail": "0",
                "city": "",
                "label": label,
                "origin": "pdok",
            }
        )

    for name, docs in groups.items():
        # Duplicate names: the plain name means the town its municipality is named after.
        main_doc = next(
            (doc for doc in docs if doc["gemeentenaam"].strip().lower() == name), docs[0]
        )
        add(name, main_doc["woonplaatsnaam"].strip(), main_doc)
        if len(docs) > 1:
            for doc in docs:
                province = doc["provincieafkorting"]
                add(f"{name} ({province.lower()})", f"{doc['woonplaatsnaam'].strip()} ({province})", doc)

    columns = ["name", "lat", "lon", "rail", "city", "label", "origin"]
    rows = sorted(kept + added, key=lambda row: (bool((row.get("city") or "").strip()), row["name"]))
    with TARGET.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows({column: row.get(column) or "" for column in columns} for row in rows)
    print(f"{len(kept)} hand-kept + {len(added)} from PDOK = {len(rows)} rows")


if __name__ == "__main__":
    main()
