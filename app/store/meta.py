"""SQLite meta operations."""
from __future__ import annotations

from .. import tenancy
from .connection import connect


def load_token_df() -> tuple[dict[str, int], int] | None:
    with connect() as con:
        rows = con.execute("SELECT token, n FROM token_df").fetchall()
        total = con.execute("SELECT value FROM meta WHERE key='token_df_total'").fetchone()
    if not rows or total is None:
        return None
    return {row["token"]: int(row["n"]) for row in rows}, int(total["value"])



def save_token_df(counter: dict[str, int], total: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM token_df")
        con.executemany(
            "INSERT INTO token_df(token, n) VALUES (?, ?)",
            [(token, int(count)) for token, count in counter.items()],
        )
        con.execute(
            "INSERT INTO meta(key,value) VALUES ('token_df_total',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(total),),
        )



def meta_get(key: str, default: str | None = None) -> str | None:
    with connect() as con:
        if key in (
            "scan_status",
            "scan_summary",
            "ownership_v1",
            "token_df_total",
            "scheduler_last_fire",
        ):
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        else:
            row = con.execute(
                "SELECT value FROM user_meta WHERE owner=? AND key=?", (tenancy.user_id(), key)
            ).fetchone()
    return row["value"] if row else default



def meta_set(key: str, value: str) -> None:
    with connect() as con:
        if key in (
            "scan_status",
            "scan_summary",
            "ownership_v1",
            "token_df_total",
            "scheduler_last_fire",
        ):
            con.execute(
                "INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        else:
            con.execute(
                "INSERT INTO user_meta(owner,key,value) VALUES (?,?,?) ON CONFLICT(owner,key) DO UPDATE SET value=excluded.value",
                (tenancy.user_id(), key, value),
            )

