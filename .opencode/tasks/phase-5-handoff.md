# Jobcraft handoff — Phase 5 remainder, Phases 6–7

Recorded 2026-10-24. This is the authoritative starting point for a new session.

## Current state

- Branch `main`, HEAD `76ef373` ("Drive hard filters from Jev signals (#24)").
- Working tree clean; `main` in sync with `origin/main`.
- No open pull requests.
- **195 tests pass** (`uv run pytest -q`), Ruff clean, CI `quality` green.
- Remote beta `https://tinystormradio.nl/danny/` still runs the drawer-era build
  (`8178e6a`) and has **not** received multiuser or Phase 3–5 work.
- Local app: `LLM_BASE_URL=... LLM_API_KEY=... APP_USER=owner APP_PASSWORD=dev-local
  uv run uvicorn app.main:app --port 8080`. `APP_SKIP_LLM=1` renders pages without
  an LLM call.

## Objective

Finish the phased brief (Phases 0–7), one work item per commit and one
phase/sub-phase per PR, continuing automatically while authorized work remains.

Constraints that still apply:

- Local-first. Do not deploy or touch the remote beta unless explicitly asked.
- Do not read, copy or commit `data/`, `seed/`, `.runtime/` or any `.env*`.
  Never run destructive resets in this working copy; it holds real account data.
- Preserve the existing account (`legacy`) and its partially bootstrapped state.
  New accounts start empty.
- Shared vacancy catalogue/sources; private profiles, conversations, votes,
  matches, documents and usage per account.
- No paid provider calls from tests; use fakes or `httpx.MockTransport`.
- Keep `uv`, `ruff` (line length 100, E/F/I/UP) and `pytest`; no second
  formatter/linter.
- Dutch UI, English code/comments/docs.

Definition of done per PR:

```bash
uv run pytest -q
uv run ruff check .
node --check app/static/job-drawer.js   # or every changed app/static/*.js
docker build -t jobcraft:local .
```

Then open a PR, wait for the required `quality` check, and merge with a squash
merge. Do not force-push (policy denies it); merge `main` into the branch to
retrigger CI when needed.

## Completed (phase 0 → Phase 5 partial)

- Phase 0: test isolation, network block, disposable paths, factories — `e059c2e`.
- Phase 1 B1–B12 fixes — `93d1c8e`.
- Phase 2 direct OpenAI-compatible client, plain prompts, role models, bounded
  history, structured contracts — `8632358`.
- Phase 3 foundation: versioned migrations, backups, state tables, file import,
  SQLite-first state, `Settings`/`Workspace` — `ea8b80a`.
- Phase 4: queue, worker loop, task API, worker lifecycle, scheduler, durable
  rerank/match/consolidation/document, match-after-scan fan-out, daily budget,
  durable manual scan and task list API — PRs #5–#15, `main` reached `644599d`.
- Phase 5 so far:
  - Word-boundary place matching — `f3e056e` (#16).
  - Home/travel settings: `HomeBase.car_available`, `/profile/home` form and
    route, gazetteer resolution, manual coordinates — `eabcee1`, `8f8fd8b` (#23).
  - Neutral consolidation prompt and interview topics — `f5bb8ca` (#17),
    `287471e` (direct).
  - Interview coverage threshold 3 — `287471e`.
  - Gazetteer CSV `app/data/places_nl.csv` (68 places + rail flag) loaded over
    the built-in fallback — `03eda4d` (direct).
  - Per-account source enablement: `user_sources` migration, owner-scoped
    helpers, filtering in `/jobs` and matching, toggle UI — `2d25566` (#18),
    `ad9e954` (#20), `6e1fb29` (#21).
  - CV heading aliases — `7b80a58` (#22).
  - Signal-driven hard filters — `76ef373` (#24).

Three commits (`eabcee1`, `287471e`, `03eda4d`) landed directly on `main` because
the branch was not created first. Do not repeat that: **create the branch before
editing**.

## Remaining Phase 5

### P5.5 — variable 3–8 CV strong points

This is the next concrete task. Read these anchors first:

- `app/docs.py:60-120` — `ensure_templates`, `_extract_identity`.
- `app/docs.py:148-175` — `cv_identity`, `parse_cv_tailoring`.
- `app/docs.py:192-205` — `cv_prompt`.
- `app/docs.py:275-290` — `render_cv`.
- `app/docs.py:414-435` — `generate_document` call sites.

Current hard-coded contracts:

- `ensure_templates` stops collecting at five bullets and rejects anything other
  than exactly five (`len(strong_points) != 5`).
- `cv_prompt` asks for "vijf" points.
- `parse_cv_tailoring` requires exactly five.
- `render_cv` requires exactly six replacements (one profile + five bullets).
- The early-return path for an existing user template writes `identity.json`
  without any bullet count.

Required changes:

1. Collect strong points until the section ends (a non-`List Bullet` paragraph
   or the next heading), not at a fixed count. Require 3–8.
2. Add a helper that counts `{{STRONG_n}}` placeholders in the template and store
   `"strong_count"` in `identity.json`. Use it in both the fresh-template and
   existing-template paths, so old five-bullet templates keep working.
3. `cv_prompt(job, profile_md, strong_count)` must state the actual count.
4. `parse_cv_tailoring(text, strong_count)` must require exactly that count.
5. `render_cv` must expect `1 + len(tailoring["strong_points"])` replacements
   and raise a clear error if the template has a different number of
   placeholders.
6. `generate_document` reads `cv_identity().get("strong_count", 5)` and passes it
   to the prompt and parser.
7. Keep five as the default when `identity.json` predates `strong_count`.
8. Tests: document fixtures with 3, 5 and 8 bullets; invalid 2 and 9; renderer
   placeholder mismatch; existing CV tests still pass. Prefer in-memory
   `python-docx` fixtures; do not add real personal documents.

Likely branch name: `phase-5-cv-bullets`.

### P5.6 — national gazetteer coverage

- `app/data/places_nl.csv` currently has 68 places; `app/geo.py` loads it over
  the hard-coded `PLACES`/`RAIL_PLACES` fallback.
- Expand the CSV to national coverage (all municipalities and reasonable towns),
  with a correct `rail` flag. Keep names lowercase and unique.
- Word-boundary matching and manual coordinates are already implemented.
- Unknown places deliberately yield `in_range=None`; do not turn them into a
  veto.
- Decide and document whether the hard-coded fallback stays as an offline
  fallback or is deleted once the CSV is authoritative. Default: keep it until
  the CSV is complete, then remove it and test the missing-file case.
- Tests: CSV loads, a non-Randstad place resolves, rail/non-rail transit
  differs, missing CSV behaves predictably.

## Phase 3 and Phase 4 residuals

Fold these in when touching the relevant paths; do not mix them into unrelated
behaviour changes.

- Phase 3: relocate the legacy workspace to the per-owner layout; remove the
  remaining `LEGACY_ID` special-casing; add a baseline fixture from `718fc48`;
  the owner must run the real-data migration (agents never touch `data/`).
- Phase 4: add UI polling for task status; remove the `meta` status keys once
  tasks are the sole source of truth; add one end-to-end worker test
  (enqueue → worker → persisted result) with no network access.
- Migration test `tests/test_migrations.py` asserts `PRAGMA user_version == 5`;
  bump it when adding `m0006`.

## Phase 6 — application structure (not started)

Current sizes: `app/main.py` 1181 lines, `app/store.py` 1089 lines. No
`app/routers/` package yet.

Suggested order, one behaviour-preserving PR per step:

1. **Auth dependencies.** Extract `require_user`, CSRF and owner resolution into
   a dependencies module; keep the same redirects and error shapes. Tests.
2. **Routers.** Add `app/routers/` for jobs, profile, interview, documents,
   sources and admin; move handlers with `APIRouter`; leave `app/main.py` as
   wiring/lifespan only. URLs and template contexts must not change.
3. **Scoring package.** Move `matching.rule_score`, the rank blend and feedback
   adjustments into `app/scoring/`. Preserve 0.35 rule / 0.40 Jev fit / 0.25 raw
   verdict and the rule that the verdict cache excludes feedback. Golden tests.
4. **Store split.** Split by concern (jobs, tasks, users/sources,
   documents/usage) behind a shared connection module. Re-export public names or
   update call sites consistently; do not touch the migration chain in the same
   PR.
5. **Source registry.** One place for adapter kinds, labels and config
   validation. New sources still require `vacaturebron-recon` evidence and
   verified content access before enabling.

## Phase 7 — deployment hardening (not started)

- Trust only configured proxy IPs for forwarded headers.
- Secure cookie/header defaults.
- Subdomain default per the brief decision; the remote beta path stays untouched
  until explicitly requested.
- Non-root container user and correct file permissions.
- Deploy-time database backups.
- Verify container parity: `docker build -t jobcraft:local .`, app starts as
  non-root, no browser or local-model dependency.
- Agents do not deploy, push images or open release PRs; leave the branch and
  commits ready and report them.

## Decisions already made — do not reopen without cause

- Scoring after B3: 0.35 rule / 0.40 Jev fit / 0.25 raw LLM verdict.
- Verdict cache key excludes feedback; votes are applied by the deterministic
  ranker, so a vote costs $0.
- In-process SQLite task queue, no Celery/Redis.
- Keep legacy account id `legacy`; preserve its credentials and state.
- Daily LLM budget $0.50; scans 07:30 and 17:30; `JOB_STALE_DAYS=21`.
- Multi-account is a real goal, not a demo.
- No type checker; `uv` + `ruff` + `pytest` only.
- Subdomain default for Phase 7; do not rename existing cookies.
- Gazetteer: shipped CSV plus manual coordinates for unknown places.

## Known limitations

- Phase 5 is incomplete (variable CV bullets, national gazetteer).
- Remote beta is not on the multiuser build.
- The legacy file-to-SQLite migration still requires an owner-run step on real
  data.
- No end-to-end worker test; worker components are tested separately.
- CI covers pytest, Ruff and one JS file; extend the `node --check` list when
  more scripts change.

## Next action

Start `phase-5-cv-bullets`:

```bash
git switch -c phase-5-cv-bullets
```

Implement P5.5 exactly as listed, run the full checks, open a PR, wait for green
`quality`, squash-merge, then continue with P5.6 (gazetteer), then Phase 6.
