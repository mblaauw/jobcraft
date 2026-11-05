"""List filters that run in SQL on the words a vacancy actually uses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .connection import connect

# Words a vacancy must contain to pass a filter; a vacancy that says nothing does not.
FILTER_TERMS = {
    "remote": ("thuiswerk", "hybride", "hybrid", "remote", "deels thuis", "vanuit huis", "work from home"),
    "vast": ("vast contract", "vaste aanstelling", "vast dienstverband", "vaste baan", "permanent contract", "permanent position"),
    "tijdelijk": ("tijdelijk", "jaarcontract", "bepaalde tijd", "detachering", "fixed-term", "fixed term", "temporary"),
    "freelance": ("freelance", "zzp", "interim", "contractor"),
    "salaris": ("€", "eur "),
}


HOUR_BUCKETS = {
    "tot24": (8, 12, 16, 20, 24),
    "24-32": (24, 26, 28, 30, 32),
    "32-40": (32, 34, 36, 38, 40),
}


def list_filter_clauses(filters: dict | None) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []
    bucket = HOUR_BUCKETS.get((filters or {}).get("hours") or "")
    if bucket:
        terms = [f"{hours} {word}" for hours in bucket for word in ("uur", "hours")]
        clauses.append(
            "("
            + " OR ".join("search_text LIKE ? OR COALESCE(hours,'') LIKE ?" for _ in terms)
            + ")"
        )
        for term in terms:
            params.extend([f"%{term}%", f"%{term}%"])
    for key in ("remote", "contract", "salaris"):
        term_key = (filters or {}).get(key)
        terms = FILTER_TERMS.get(term_key) if term_key else None
        if terms:
            clauses.append("(" + " OR ".join("search_text LIKE ?" for _ in terms) + ")")
            params.extend(f"%{term}%" for term in terms)
    if (filters or {}).get("auto"):
        # The travel estimate already read the text, negations ("geen bedrijfsauto") included.
        clauses.append("json_extract(travel_json,'$.auto') = 1")
    days = (filters or {}).get("days")
    if days:
        cutoff = (datetime.now(UTC) - timedelta(days=int(days))).isoformat()
        clauses.append("COALESCE(posted_at, first_seen) >= ?")
        params.append(cutoff)
    seen_after = (filters or {}).get("seen_after")
    if seen_after:
        clauses.append("first_seen > ?")
        params.append(seen_after)
    return clauses, params


def full_text_share_by_source(minimum: int = 500) -> dict[str, int]:
    """Percentage of a source's vacancies with enough text to judge them on."""
    with connect() as con:
        rows = con.execute(
            "SELECT COALESCE(source_id,'?') AS source_id, "
            "ROUND(100.0 * SUM(LENGTH(COALESCE(description,'')) >= ?) / COUNT(*)) AS share "
            "FROM jobs GROUP BY source_id",
            (minimum,),
        ).fetchall()
    return {row["source_id"]: int(row["share"]) for row in rows}
