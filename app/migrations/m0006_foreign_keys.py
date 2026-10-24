"""Fail-closed relationship validation and transactional child-table rebuilds."""

import re
import sqlite3

RELATIONS = {
    "job_states": ("jobs", "job_id", "id"),
    "feedback": ("jobs", "job_id", "id"),
    "documents": ("jobs", "job_id", "id"),
    "messages": ("sessions", "session_id", "id"),
}


def _preflight(con: sqlite3.Connection) -> None:
    failures = []
    for child, (parent, foreign, primary) in RELATIONS.items():
        owner = " AND p.owner=c.owner" if child == "messages" else ""
        count = con.execute(
            f"SELECT COUNT(*) FROM {child} c LEFT JOIN {parent} p "
            f"ON p.{primary}=c.{foreign}{owner} WHERE c.{foreign} IS NOT NULL AND p.{primary} IS NULL"
        ).fetchone()[0]
        if count:
            failures.append(f"{child}->{parent}={count}")
    if failures:
        raise RuntimeError("Foreign key preflight failed: " + ", ".join(failures))


def _rebuild(con: sqlite3.Connection, table: str, constraint: str) -> None:
    if table not in RELATIONS:
        raise ValueError("Unknown relationship table")
    sequence = con.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
    sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    indexes = [
        row[0]
        for row in con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        )
    ]
    temporary = table + "__foreign_keys"
    renamed, replacements = re.subn(
        r'(?i)^(CREATE TABLE(?: IF NOT EXISTS)?\s+)"?' + re.escape(table) + r'"?(?=\s*\()',
        lambda match: match[1] + temporary,
        sql,
        count=1,
    )
    if replacements != 1:
        raise RuntimeError("Unexpected table definition for " + table)
    before, after = renamed.rsplit(")", 1)
    con.execute(before + ", " + constraint + ")" + after)
    columns = [row[1] for row in con.execute(f"PRAGMA table_info({table})")]
    names = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
    con.execute(f"INSERT INTO {temporary} ({names}) SELECT {names} FROM {table}")
    con.execute(f"DROP TABLE {table}")
    con.execute(f"ALTER TABLE {temporary} RENAME TO {table}")
    for statement in indexes:
        con.execute(statement)
    if sequence is not None:
        changed = con.execute(
            "UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?", (sequence[0], table)
        )
        if not changed.rowcount:
            con.execute("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", (table, sequence[0]))


def up(con: sqlite3.Connection) -> None:
    # Caller owns BEGIN/COMMIT; no executescript (which can implicitly commit).
    _preflight(con)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS sessions_id_owner ON sessions(id,owner)")
    for child, (parent, foreign, primary) in RELATIONS.items():
        constraint = (
            f"FOREIGN KEY({foreign},owner) REFERENCES {parent}({primary},owner)"
            if child == "messages"
            else f"FOREIGN KEY({foreign}) REFERENCES {parent}({primary})"
        )
        _rebuild(con, child, constraint)
    if con.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("Foreign key validation failed")
