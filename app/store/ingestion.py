"""SQLite ingestion operations."""
from __future__ import annotations

from .connection import connect, now
from .jobs import normalize_posted_at
from .schema import _search_text


def upsert_job(job: dict) -> tuple[int, bool]:
    stamp = now()
    job = {**job, "posted_at": normalize_posted_at(job.get("posted_at"))}
    search_text = _search_text(job.get("title"), job.get("company"), job.get("description"))
    with connect() as con:
        existing = con.execute("SELECT id FROM jobs WHERE url=?", (job["url"],)).fetchone()
        if existing:
            con.execute(
                "UPDATE jobs SET source_id=?, title=?, company=?, location=?, hours=?, "
                "description=?, posted_at=?, last_seen=?, content_hash=?, lat=?, lon=?, "
                "search_text=? WHERE id=?",
                (
                    job.get("source_id"),
                    job.get("title"),
                    job.get("company"),
                    job.get("location"),
                    job.get("hours"),
                    job.get("description"),
                    job.get("posted_at"),
                    stamp,
                    job.get("content_hash"),
                    job.get("lat"),
                    job.get("lon"),
                    search_text,
                    existing["id"],
                ),
            )
            return existing["id"], False
        cursor = con.execute(
            "INSERT INTO jobs(source_id, url, title, company, location, hours, description, "
            "posted_at, first_seen, last_seen, content_hash, lat, lon, search_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.get("source_id"),
                job["url"],
                job.get("title"),
                job.get("company"),
                job.get("location"),
                job.get("hours"),
                job.get("description"),
                job.get("posted_at"),
                stamp,
                stamp,
                job.get("content_hash"),
                job.get("lat"),
                job.get("lon"),
                search_text,
            ),
        )
        return cursor.lastrowid, True

