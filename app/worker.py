"""The background worker: scans, assessments, documents and digests, around the clock.

Run with `python -m app.worker`. The web app only queues this work, so either side can
restart without disturbing the other.
"""

from __future__ import annotations

import logging
import signal
import sqlite3
import threading

from . import runtime, store, workflows  # noqa: F401 - workflows registers the task handlers
from .util import now_iso

HEARTBEAT_SECONDS = 30.0
log = logging.getLogger("jobcraft")
_STOP = threading.Event()


def beat() -> None:
    store.meta_set("worker_heartbeat", now_iso())


def run() -> None:
    _STOP.clear()
    with runtime.services("worker"):
        log.info("worker started")
        while True:
            try:
                beat()
            except sqlite3.OperationalError as exc:  # Busy database: keep working, beat next time.
                log.warning("worker heartbeat skipped: %s", exc)
            if _STOP.wait(HEARTBEAT_SECONDS):
                break
    log.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, lambda *_: _STOP.set())
    signal.signal(signal.SIGINT, lambda *_: _STOP.set())
    run()


if __name__ == "__main__":
    main()
