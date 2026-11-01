"""List filters that run in SQL on the words a vacancy actually uses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Words a vacancy must contain to pass a filter; a vacancy that says nothing does not.
FILTER_TERMS = {
    "remote": ("thuiswerk", "hybride", "hybrid", "remote", "deels thuis", "vanuit huis", "work from home"),
    "vast": ("vast contract", "vaste aanstelling", "vast dienstverband", "vaste baan", "permanent contract", "permanent position"),
    "tijdelijk": ("tijdelijk", "jaarcontract", "bepaalde tijd", "detachering", "fixed-term", "fixed term", "temporary"),
    "freelance": ("freelance", "zzp", "interim", "contractor"),
    "salaris": ("€", "eur "),
}


def list_filter_clauses(filters: dict | None) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []
    for key in ("remote", "contract", "salaris"):
        term_key = (filters or {}).get(key)
        terms = FILTER_TERMS.get(term_key) if term_key else None
        if terms:
            clauses.append("(" + " OR ".join("search_text LIKE ?" for _ in terms) + ")")
            params.extend(f"%{term}%" for term in terms)
    days = (filters or {}).get("days")
    if days:
        cutoff = (datetime.now(UTC) - timedelta(days=int(days))).isoformat()
        clauses.append("COALESCE(posted_at, first_seen) >= ?")
        params.append(cutoff)
    return clauses, params
