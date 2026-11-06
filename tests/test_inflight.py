"""The deploy names the rounds in flight before it restarts, and stays quiet otherwise."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("inflight", Path(__file__).parents[1] / "deploy" / "inflight.py")
inflight = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inflight)


def test_names_running_and_queued_rounds_and_is_quiet_otherwise(tmp_path, capsys):
    database = tmp_path / "app.db"
    con = sqlite3.connect(database)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, kind TEXT, status TEXT, started_at TEXT, created_at TEXT)")
    con.executemany(
        "INSERT INTO tasks(kind, status, started_at, created_at) VALUES (?,?,?,?)",
        [
            ("scan", "running", "2026-11-06T16:40:00+00:00", "2026-11-06T16:40:00+00:00"),
            ("match", "queued", None, "2026-11-06T16:41:00+00:00"),
            ("scan", "done", "2026-11-06T08:20:00+00:00", "2026-11-06T08:20:00+00:00"),
        ],
    )
    con.commit()
    con.close()
    assert inflight.inflight(database) == [
        "ophaalronde loopt sinds 2026-11-06 16:40",
        "beoordelingsronde wacht sinds 2026-11-06 16:41",
    ]
    assert inflight.main(["--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "werk in uitvoering" in out and "ophaalronde loopt sinds 2026-11-06 16:40" in out

    assert inflight.main(["--data", str(tmp_path / "nope")]) == 0
    assert capsys.readouterr().out == ""
    (tmp_path / "broken.db").write_text("not a database")
    assert inflight.inflight(tmp_path / "broken.db") == []
