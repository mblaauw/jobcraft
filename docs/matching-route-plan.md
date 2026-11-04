# One route per vacancy: making System 3 explicit

Status: step 1 approved and built on 2026-11-04 (branch `route-gate`); step 2
waits for the live measurement. Spike run on 2026-11-04 against the local data.

## Why

Matching is already trimodal: Jev (System 1) answers typed questions with a
certainty, the ranker (System 3) blends and filters deterministically, and the
LLM matcher (System 2) judges the rest. What is missing is the diamond in the
middle as one thing: today the decision "what happens next with this vacancy"
is spread over five places, each with its own threshold, and one of them
(opening a vacancy) ignores Jev's certainty altogether.

| Decision today | Where | Threshold |
| --- | --- | --- |
| escalate flag | `systemone.signals_from_answers` | `SYSTEMONE_CONFIDENCE` 0.75 |
| rank floor, fresh verdict, voted, too far | `ranking.escalation_candidates` | `MATCH_MIN_RANK` 35 |
| batch size, budget | `matching.match_jobs`, `workflows._run_match` | `MATCH_BATCH`, `MATCH_DAILY_BUDGET_USD` |
| buy a verdict on open, whatever Jev said | `job_verdict.request` | none |
| which vacancies to ask a person about | `judgements.pick` | reads the flag again |

## What the spike showed (local data, 340 vacancies, signals from 18 October)

| Route outcome | Count | Note |
| --- | --- | --- |
| excluded | 144 | 137 too far, 7 voted |
| wait (no Jev fit yet) | 88 | |
| act (rank below floor) | 78 | never worth a verdict |
| act (Jev sure) | 0 | old certainty formula; PR 77 changed it on 3 November |
| escalate | 30 | |
| verdict (fresh) | 0 | every stored verdict is stale against the current digest |

Two leaks, in numbers:

| Vacancies | Today | With the route |
| --- | --- | --- |
| 167 without a verdict, opened one by one | 167 verdicts bought, 95 of them for vacancies that are too far or handled | 95 excluded, 19 act (below the floor), 53 wait: Jev first, then only the unsure |
| 171 with a verdict gone stale after a profile edit | every one above the floor re-bought | 30 escalate, 59 act (Jev sure), 35 wait, 47 excluded |

So the on-open path is the biggest leak and the profile edit the second; both
spend regardless of how sure Jev is.

The vacancy reader was also measured: the free parser gives a usable structure
for 77 of 261 eligible texts, but it never gives the summary and must-haves the
LLM reading adds. A rule gate there saves little and loses something. Out of
scope.

## Goal

One function decides, for one vacancy, what happens next, and says why. Every
caller uses it. Its outcome is counted per round and visible on the vacancy, so
the gate can be measured on live data and tuned from the judgements people give.

## Design

### `app/scoring/route.py`

```python
@dataclass(frozen=True)
class Route:
    outcome: Literal["excluded", "verdict", "wait", "act", "escalate"]
    reason: str          # one short Dutch phrase for the page and the summary

def route(job, signals, digest, *, rank, wanted=False, thresholds=None) -> Route
```

Order of the rules, first match wins:

1. voted, handled status, or too far → `excluded`
2. a fresh LLM verdict for the current digest → `verdict`
3. `wanted` (the person asked for a deeper assessment) → `escalate`
4. no Jev fit yet → `wait` while Jev is configured; without Jev the matcher
   judges as before (`escalate`, or `act` below the floor), so a deployment
   without a Jev key keeps working
5. rank below `MATCH_MIN_RANK` → `act` ("te laag gerangschikt")
6. Jev certainty at or above `SYSTEMONE_CONFIDENCE` → `act` ("snelle beoordeling is zeker")
7. otherwise → `escalate`

`thresholds` is one small frozen dataclass read from the existing environment
names at call time (today `MIN_RANK_FOR_LLM` is read once at import, which
tests cannot override), so nothing changes for deployments. The function is
pure: no store access, no travel lookups; callers pass what they already have.
A job with signals but no stored rank gets one from `rank_for` by the caller
before routing; a job without signals is `wait` whatever its rank.

Decision to confirm: on open, a vacancy Jev is sure about is `act` even when it
is a top match. The verdict's prose (why, concerns, questions for the employer)
is shown only on the assessment panel and as the one-line reason on a card,
which falls back to the rule-based line. So `act` costs one sentence and the
deeper assessment stays one click away. The alternative, escalating on open
for high-ranked sure fits, keeps most of today's leak; not recommended.

### Callers

- `ranking.escalation_candidates` becomes: route every job, keep `escalate`,
  sort by rank, cut at the limit. Behaviour change: a stale verdict with a sure
  Jev fit is no longer re-bought after a profile edit.
- `matching.match_jobs` routes every job once per round (`ranking.routed`) and
  takes the sample and the candidates from that pass; `rerank_all` caches the
  route counts and the queue depth together, and the round summary carries the
  counts to the "Laatste beoordelingsronde" disclosure.
- `job_verdict.request` (on open) routes first:
  - `escalate` → as today, enqueue the verdict task.
  - `act` → enqueue nothing. `job_verdict.context` returns the route, and the
    assessment panel shows the quick assessment with its certainty, the facts
    chips, and a button "Beoordeel dieper". The current `elif signals` text
    ("daglimiet bereikt") is replaced by the route's reason, in the person's
    words: the grade Jev gave ("past goed", from the fit value mapped back to
    the five grades) and how sure it was. Budget or AI problems keep their own
    messages; the button is hidden while the budget is reached.
  - `wait` → enqueue the same verdict task, not a second kind: one task kind
    and one dedupe key keep the page's pending swap working. The handler asks
    Jev for this one vacancy first through `matching.signal_one(job)`, pulled
    out of `_system_one_round` so the call logic exists once (shared facts
    from `load_job_facts` when known, then the personal fit: about a second),
    stores the signals, re-ranks
    this job cheaply through a new `ranking.after_signals` that mirrors
    `after_verdict`, routes again, and buys a verdict only on `escalate`. The
    task's result records the route, so the swapped-in panel can show it.
  - Jev not configured, or its daily limit reached → behave as today (a
    verdict, budget permitting): the person opened it and wants an answer.
- "Beoordeel dieper" posts to `/jobs/{id}/oordeel/dieper`, stores
  `verdict_wanted:{job_id}` in user meta, and enqueues the verdict task. The
  flag is read by `route` as `wanted`, never enters any digest or cache key,
  and is cleared when the verdict lands, so a later profile edit does not
  re-buy that verdict forever.
- `judgements.pick` keeps its own selection, but reads "unsure" as
  `route(...).outcome == "escalate"` so both gates agree.
- `profile` edits: a changed profile stales every verdict and queues a match
  round; with the route, that round re-buys only the unsure ones. No change
  to the trigger itself.

### Keeping the reference signal honest

The verdicts are also what the cheap parts are tuned against: `fit_calibration`
maps Jev's scale onto the verdict scale from at least twenty paired jobs, and
`taste.label` learns from fresh verdicts. If `act` stops buying verdicts for
every sure fit, the pairs come only from the unsure band and the calibration
bends toward it. So each background round keeps a small sample: after the
`escalate` candidates, `MATCH_SAMPLE` (default 2) of the highest-ranked `act`
jobs with a sure Jev fit and no fresh verdict are judged too. The route
outcome for those is still `act`; the sample is the round's choice and is
counted separately in the summary ("steekproef"). On open, nothing is sampled.

The judged report in `scripts/matching_quality.py` already measures the risk
that `act` introduces: good vacancies (people said "ja") that a sure Jev "no"
would hide. That number gates the on-open step (see Acceptance).

Rollback lever: `SYSTEMONE_CONFIDENCE=1.01` makes every signalled job
`escalate` again, which is today's behaviour. No new flag.

### Measurement

- `scripts/matching_quality.py` prints the route outcome counts and the share of
  signalled vacancies that skipped the LLM. The existing judged report already
  measures the risk that matters for `act`: good vacancies that a sure Jev
  "no" would hide.
- After deploy, run it on the VPS (read-only) and record the share in the
  project notes before touching any threshold.

### Accounts and privacy

Nothing new leaves the machine. The route is computed locally from stored
signals. The Jev-first call on open sends the same state as the background
round: vacancy text, the public excerpt and public preferences. The `wanted`
flag, the sample and the route counts are per account in user meta; facts
stay shared as today.

## Out of scope

- Any change to the verdict cache key or the blend weights.
- Jev for letters, CVs or ingestion.
- The vacancy reader gate (see spike).

## Acceptance

- `uv run pytest -q` and `ruff` green; new tests for every rule in `route` and
  their order, the thresholds read at call time, the calibration sample, the
  one-shot `wanted` flag, `after_signals`, the on-open `act` and `wait` paths
  (including Jev unavailable), the button, and the summary counts.
- Opening a vacancy Jev is sure about buys no verdict, and the page explains
  why in the person's words, with the deeper assessment one click away.
- A profile edit no longer re-buys verdicts for vacancies Jev is sure about.
- The quality script reports routes on the live data; the number is written
  down.
- Before step 2 ships, the judged report on live data shows no good vacancy
  hidden by a sure Jev "no" (or the threshold is raised until it does, and
  that is written down too). With fewer judgements than the script's `ENOUGH`,
  step 2 waits for them: five a day come in from Vandaag.

## Steps

1. **The route, measured.** `route.py` with thresholds and tests;
   `escalation_candidates` and `judgements.pick` use it; the calibration
   sample; summary counts and the disclosure line; the quality script prints
   route counts and the share of signalled vacancies that skipped the LLM.
   Deploy, run the script on the live data read-only, write the share and the
   judged report down in the project notes. No UI change beyond the
   disclosure line; behaviour change only for stale verdicts of sure fits.
2. **On open.** Gated by the judged report (Acceptance). `act` and `wait`
   paths, `signal_one`, `after_signals`, the button with its one-shot flag,
   the panel text. `career-gui-review` on the panel before merge.

Two PRs on feature branches, each with `review` and `verifier` on the frozen
diff before merge. No new spend: the on-open Jev call costs what one Jev call
costs and replaces an LLM call; the sample is two verdicts a round, inside the
existing batch and budget.
