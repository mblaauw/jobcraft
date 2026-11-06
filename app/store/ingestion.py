"""SQLite ingestion operations."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from .connection import connect, now
from .jobs import normalize_posted_at
from .schema import _search_text


def record_detail_fetch(url: str) -> None:
    key = "detail_fetched:" + hashlib.sha256(url.encode()).hexdigest()
    with connect() as con:
        con.execute(
            "INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, now()),
        )


def fetched_within(url: str, max_age_seconds: int) -> bool:
    """Whether this page was read within the given time, whatever came of it."""
    key = "detail_fetched:" + hashlib.sha256(url.encode()).hexdigest()
    with connect() as con:
        stamp = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(stamp[0])).total_seconds() if stamp else -1
    except (ValueError, TypeError):
        return False
    return 0 <= age < max_age_seconds


def touch_recent_detail(url: str, max_age_seconds: int = 3 * 86400) -> bool:
    """Touch known rich jobs without extending their last real detail-fetch time."""
    if not fetched_within(url, max_age_seconds):
        return False
    with connect() as con:
        cursor = con.execute(
            "UPDATE jobs SET last_seen=? WHERE url=? AND description IS NOT NULL AND TRIM(description)<>''",
            (now(), url),
        )
        return bool(cursor.rowcount)


def source_urls_seen_longest_ago(source_id: str, limit: int) -> list[str]:
    """A source's vacancies that have gone longest without being seen, oldest first."""
    with connect() as con:
        rows = con.execute(
            "SELECT url FROM jobs WHERE source_id=? ORDER BY last_seen ASC LIMIT ?", (source_id, limit)
        ).fetchall()
    return [row["url"] for row in rows]


def known_job_urls(urls: list[str]) -> set[str]:
    """Which of these vacancy URLs another source already brought in."""
    urls = [url for url in urls if url]
    if not urls:
        return set()
    with connect() as con:
        rows = con.execute(
            f"SELECT url FROM jobs WHERE url IN ({','.join('?' for _ in urls)})", urls
        ).fetchall()
    return {row["url"] for row in rows}


def upsert_job(job: dict) -> tuple[int, bool]:
    stamp = now()
    job = {**job, "posted_at": normalize_posted_at(job.get("posted_at"))}
    search_text = _search_text(job.get("title"), job.get("company"), job.get("description"))
    with connect() as con:
        existing = con.execute("SELECT * FROM jobs WHERE url=?", (job["url"],)).fetchone()
        if existing:
            merged_content = False
            for key in (
                "source_id",
                "title",
                "company",
                "location",
                "hours",
                "description",
                "posted_at",
                "lat",
                "lon",
            ):
                value = job.get(key)
                if value is None or (isinstance(value, str) and not value.strip()):
                    if key in ("title", "description") and existing[key]:
                        merged_content = True
                    job[key] = existing[key]
            if merged_content:
                job["content_hash"] = hashlib.sha256(
                    f"{job['title']}:{job['description']}".encode()
                ).hexdigest()
            search_text = _search_text(job.get("title"), job.get("company"), job.get("description"))
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
