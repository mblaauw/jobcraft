"""Links on the vacancy list: status tabs and the chips that remove one filter each."""

from __future__ import annotations

from urllib.parse import urlencode

STATUSES = (("", "Alle"), ("new", "Nieuw"), ("saved", "Bewaard"), ("applied", "Gereageerd"), ("ignored", "Genegeerd"))


def links(params: dict, status_totals: dict, source_names: dict, within: bool, stale: bool) -> tuple[list, list]:
    def url(**changes) -> str:
        return "/jobs?" + urlencode({**params, **changes})

    total = sum(status_totals.values())
    tabs = [
        {"label": label, "value": value, "count": status_totals.get(value, 0) if value else total, "url": url(status=value)}
        for value, label in STATUSES
    ]
    chips = []
    for key, label in (
        ("q", f"Zoekterm: {params['q']}"),
        ("source", f"Bron: {source_names.get(params['source'], params['source'])}"),
        ("remote", "Thuiswerken genoemd"),
        ("auto", "Met bedrijfsauto"),
        ("contract", f"Contract: {params['contract']}"),
        ("salaris", "Salaris genoemd"),
        ("dagen", f"Laatste {params['dagen']} dagen"),
        ("uren", f"Uren: {params['uren']}"),
    ):
        if params.get(key):
            chips.append({"label": label, "url": url(**{key: ""})})
    if "reis" in params:
        own = {key: value for key, value in params.items() if key not in ("reis", "met", "vanaf")}
        chips.append({"label": "Ander reisbereik", "url": "/jobs?" + urlencode(own)})
    if not within:
        chips.append({"label": "Ook buiten reisbereik", "url": url(within="1")})
    if stale:
        chips.append({"label": "Verlopen vacatures", "url": url(stale="")})
    return tabs, chips
