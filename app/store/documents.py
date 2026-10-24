"""SQLite documents operations."""
from __future__ import annotations

from .. import tenancy
from .connection import connect, now


def add_document(
    job_id: int,
    kind: str,
    version: int,
    docx_path: str,
    pdf_path: str | None,
    meta_json: str,
) -> int:
    with connect() as con:
        cursor = con.execute(
            "INSERT INTO documents(job_id, kind, version, docx_path, pdf_path, meta_json, created_at,owner) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, kind, version, docx_path, pdf_path, meta_json, now(), tenancy.user_id()),
        )
        return cursor.lastrowid



def list_documents(job_id: int) -> list:
    with connect() as con:
        return con.execute(
            "SELECT * FROM documents WHERE job_id=? AND owner=? ORDER BY kind, version DESC",
            (job_id, tenancy.user_id()),
        ).fetchall()



def get_document(document_id: int):
    with connect() as con:
        return con.execute(
            "SELECT * FROM documents WHERE id=? AND owner=?", (document_id, tenancy.user_id())
        ).fetchone()



def count_documents() -> int:
    with connect() as con:
        return con.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE owner=?", (tenancy.user_id(),)
        ).fetchone()["n"]

