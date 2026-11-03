"""Vacancy facts are asked once for everyone; personal fit only for promising jobs."""

import pytest

from app import auth, matching, profile, ranking, store, systemone, tenancy
from tests.test_jobs_page import add_job, client  # noqa: F401


class FakeJev:
    def __init__(self):
        self.calls = []

    def __call__(self, state, questions=None, model=None):
        self.calls.append((state, sorted(questions)))
        answers = {}
        for key, question in questions.items():
            if question["type"] == "noul":
                answers[key] = {"type": "noul", "noul": 0.9}
            elif question["type"] == "score":
                answers[key] = {"type": "score", "score": 3, "confidence": 0.9}
            else:
                answers[key] = {"type": "choice", "choice": "overig", "confidence": 0.9}
        return {"answers": answers}, {"input_tokens": 10, "output_tokens": 1}, 5


def owner_id():
    with store.connect() as con:
        return con.execute("SELECT id FROM users WHERE username='test'").fetchone()["id"]


@pytest.fixture()
def jev(client, monkeypatch):  # noqa: F811
    fake = FakeJev()
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    monkeypatch.setattr(systemone, "decide", fake)
    monkeypatch.setattr(matching, "current_digest", lambda *a: "test")
    monkeypatch.setattr(ranking, "rerank_all", lambda *a: 0)
    monkeypatch.setattr(ranking, "escalation_candidates", lambda *a: [])
    monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "1000")
    monkeypatch.setenv("SYSTEMONE_BATCH", "1000")
    return fake


def run():
    summary = {"signals": 0, "errors": []}
    matching._system_one_round(profile.read_preferences(), {}, "test", summary)
    return summary


def fact_calls(jev):
    return [call for call in jev.calls if "fit" not in call[1]]


def fit_calls(jev):
    return [call for call in jev.calls if "fit" in call[1]]


def test_first_pass_asks_facts_and_fit_and_keeps_the_signal_format(jev):
    job_id = add_job()
    with tenancy.scope({"id": owner_id()}):
        run()
        signals = ranking.signals_of(store.get_job(job_id))
    assert signals["fit"] == 75.0
    assert signals["noul"]["phone_sales_main"] == 0.9
    assert signals["category"] == "overig"
    assert len(fact_calls(jev)) == 1 and len(fit_calls(jev)) == 1


def test_facts_call_carries_no_profile_or_preferences(jev):
    add_job()
    with tenancy.scope({"id": owner_id()}):
        profile.merge_sections({"Over mij": "UNIEK_PROFIEL"})
        run()
    assert "UNIEK_PROFIEL" not in fact_calls(jev)[0][0]
    assert "Voorkeuren" not in fact_calls(jev)[0][0]
    assert "UNIEK_PROFIEL" in fit_calls(jev)[0][0]


def test_second_round_costs_nothing(jev):
    add_job()
    with tenancy.scope({"id": owner_id()}):
        run()
        jev.calls.clear()
        assert run()["signals"] == 0
    assert not jev.calls


def test_profile_edit_reuses_facts_and_only_asks_fit_again(jev):
    add_job()
    with tenancy.scope({"id": owner_id()}):
        run()
        jev.calls.clear()
        profile.merge_sections({"Over mij": "Nieuw profiel"})
        run()
    assert not fact_calls(jev)
    assert len(fit_calls(jev)) == 1


def test_changed_vacancy_asks_facts_again(jev):
    job_id = add_job()
    with tenancy.scope({"id": owner_id()}):
        run()
        jev.calls.clear()
        with store.connect() as con:
            con.execute("UPDATE jobs SET content_hash='anders' WHERE id=?", (job_id,))
        run()
    assert len(fact_calls(jev)) == 1


def test_second_account_shares_facts(jev):
    add_job()
    with tenancy.scope({"id": owner_id()}):
        run()
    jev.calls.clear()
    auth._insert_user("second", "second", "Tweede", "user", "second-password-1")
    with tenancy.scope({"id": "second"}):
        run()
    assert not fact_calls(jev)
    assert len(fit_calls(jev)) == 1


def test_personal_fit_only_for_best_ranked_jobs(jev, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_FIT_TOP", "1")
    best = add_job("Beste", rank=90)
    other = add_job("Ander", rank=10)
    with tenancy.scope({"id": owner_id()}):
        run()
        assert ranking.signals_of(store.get_job(best))["fit"] is not None
        lower = ranking.signals_of(store.get_job(other))
        assert lower["fit"] is None and lower["noul"]
        jev.calls.clear()
        run()
    assert not jev.calls


def test_job_rising_into_the_top_gets_its_fit_later(jev, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_FIT_TOP", "1")
    add_job("Beste", rank=90)
    other = add_job("Ander", rank=10)
    with tenancy.scope({"id": owner_id()}):
        run()
        jev.calls.clear()
        store.set_rank_scores({other: 99})
        run()
        assert ranking.signals_of(store.get_job(other))["fit"] is not None
    assert not fact_calls(jev)


def test_daily_limit_counts_every_call_and_best_jobs_go_first(jev, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "2")
    best = add_job("Beste", rank=90)
    other = add_job("Ander", rank=10)
    with tenancy.scope({"id": owner_id()}):
        run()
        assert store.get_job(best)["typed_signals"]
        assert not store.get_job(other)["typed_signals"]
        jev.calls.clear()
        run()
    assert not jev.calls


def test_existing_answers_become_facts_without_a_call(jev):
    job_id = add_job()
    with tenancy.scope({"id": owner_id()}):
        preferences = profile.read_preferences()
        excerpt = profile.read_public_excerpt(4000)
        questions = systemone.questions_for(preferences)
        job = store.get_job(job_id)
        key = systemone.signals_key(job, excerpt, preferences=preferences, questions=questions)
        store.update_job_signals(
            job_id, 50, '{"fit": 60, "confidence": 0.9, "noul": {"guidance": 0.2}}', key, 0.9
        )
        run()
        assert not jev.calls
        saved = store.load_job_facts()[job_id]
        assert saved["answers"]["guidance"]["noul"] == 0.2
        profile.merge_sections({"Over mij": "Nieuw"})
        run()
    assert not fact_calls(jev)


def test_failures_stop_after_three_without_saving_rubbish(jev, monkeypatch):
    def broken(*args, **kwargs):
        raise systemone.SystemOneError("down")

    monkeypatch.setattr(systemone, "decide", broken)
    for number in range(5):
        add_job(f"Baan {number}")
    with tenancy.scope({"id": owner_id()}):
        summary = run()
        assert not store.load_job_facts()
    assert any("gestopt" in item for item in summary["errors"])


def test_adding_facts_does_not_invalidate_existing_signal_keys():
    job = {"content_hash": "x"}
    preferences = {"tracks": []}
    first = systemone.signals_key(job, "", preferences=preferences)
    assert first == systemone.signals_key(job, "", preferences=preferences)
    assert systemone.QUESTION_VERSION == "s1-account-categories-v1"
    assert set(systemone.QUESTIONS) == {*systemone.facts_questions(), *systemone.PERSONAL_IDS}


def test_running_out_of_calls_never_wipes_a_fit(jev, monkeypatch):
    job_id = add_job()
    with tenancy.scope({"id": owner_id()}):
        run()
        assert ranking.signals_of(store.get_job(job_id))["fit"] is not None
        profile.merge_sections({"Over mij": "Nieuw profiel"})  # the fit is due again
        monkeypatch.setenv("SYSTEMONE_DAILY_LIMIT", "0")
        jev.calls.clear()
        run()
        assert not jev.calls
        assert ranking.signals_of(store.get_job(job_id))["fit"] is not None
