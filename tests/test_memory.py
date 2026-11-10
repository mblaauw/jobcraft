"""Whole-catalogue passes take turns and hand memory back, so the worker fits its limit."""

from __future__ import annotations

import threading
import time

from app import memory, ranking, tasks


def test_catalogue_passes_take_turns_and_may_nest(monkeypatch):
    released = []
    monkeypatch.setattr(memory, "release", lambda: released.append(1))
    inside, overlap = [], []

    def pass_():
        memory.mark_task_thread()
        with memory.catalogue():  # noqa: SIM117 - the nesting is what is tested
            with memory.catalogue():  # a re-rank runs the routed pass inside itself
                inside.append(1)
                if len(inside) > 1:
                    overlap.append(1)
                time.sleep(0.05)
                inside.pop()

    threads = [threading.Thread(target=pass_) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlap == []
    assert len(released) == 6  # after every pass, nested ones included


def test_a_request_thread_never_waits_behind_a_task_pass():
    held, done = threading.Event(), threading.Event()
    finished = []

    def task_pass():
        memory.mark_task_thread()
        with memory.catalogue():
            held.set()
            done.wait(2)

    def request():
        with memory.catalogue():  # APP_ROLE=all: web and worker share the process
            finished.append(1)

    worker = threading.Thread(target=task_pass)
    worker.start()
    held.wait(2)
    web = threading.Thread(target=request)
    web.start()
    web.join(1)
    assert finished == [1]  # while the task still holds its turn
    done.set()
    worker.join()


def test_release_is_safe_where_libc_cannot_trim(monkeypatch):
    monkeypatch.setattr(memory, "_libc", None)
    memory.release()


def test_every_task_hands_its_memory_back(monkeypatch):
    released = []
    monkeypatch.setattr(memory, "release", lambda: released.append(1))
    tasks.handler("memory-probe")(lambda task: {})
    tasks.enqueue("memory-probe", owner=None, dedupe_key="memory-probe")
    tasks.handler("memory-fail")(lambda task: 1 / 0)
    tasks.enqueue("memory-fail", owner=None, dedupe_key="memory-fail")
    assert tasks.run_one() and tasks.run_one()
    assert released == [1, 1]  # a failing task too


def test_a_rerank_lets_its_first_copy_go_before_the_routed_pass(monkeypatch):
    """The routed pass reads the catalogue again; the first copy must be gone by then."""
    import weakref

    class Rows(list):
        pass

    alive = []

    def listing():
        rows = Rows()
        alive.append(weakref.ref(rows))
        return rows

    seen_alive = []

    def routed(*args, **kwargs):
        seen_alive.append(sum(1 for ref in alive if ref() is not None))
        return []

    monkeypatch.setattr(ranking.profile, "profile_exists", lambda: True)
    monkeypatch.setattr(ranking.profile, "read_preferences", lambda: {})
    monkeypatch.setattr(ranking.feedback, "load", lambda: {})
    monkeypatch.setattr(ranking, "_current_digest", lambda prefs: "d")
    monkeypatch.setattr(ranking, "refresh_travel", lambda: 0)
    monkeypatch.setattr(ranking.store, "list_jobs_for_matching", listing)
    monkeypatch.setattr(ranking.store, "set_rank_scores", lambda scores: None)
    monkeypatch.setattr(ranking.store, "meta_set", lambda key, value: None)
    monkeypatch.setattr(ranking, "fit_calibration", lambda jobs, digest: (1.0, 0.0))
    monkeypatch.setattr(ranking.taste, "scores", lambda *args: {})
    monkeypatch.setattr(ranking, "routed", routed)
    monkeypatch.setattr(ranking, "_remember_routes", lambda digest, decided: {})
    ranking.rerank_all()
    assert seen_alive == [0]
