# Jobcraft — project instructions

Local-first, multi-account FastAPI app (Jinja + SQLite + `data/`) that runs an
direct OpenAI-compatible LLM client for the LLM parts. Everything personal stays on disk
under `data/` and is git-ignored; never commit `data/`, `seed/`, `.runtime/`,
`.env.local` or generated documents.

## Commands

- Run: `LLM_BASE_URL=... LLM_API_KEY=... APP_USER=owner APP_PASSWORD=dev-local uv run uvicorn app.main:app --port 8080`
- Checks: `scripts/check.sh` (ruff, JavaScript syntax, pytest; prints the revision first).
  Pieces: `uv run pytest -q` · `uv run ruff check .` · Container: `docker build -t jobcraft:local .`
- Quality check after matching changes: `uv run python scripts/matching_quality.py`
  (legacy account) or `--user <name>` for another account.

## Architecture rules

- **Matching is two-stage.** System One (Jev via Runware `/v1/decisions`) produces
  typed signals for every job; only low-confidence/high-rank cases go to the LLM.
  The LLM verdict cache key must **never include feedback** — votes are applied by
  the deterministic ranker (`app/ranking.py`) so a vote costs $0 and stays instant.
  A local meaning index (`app/job_index.py`, `app/taste.py`) adds a free "taste"
  part from the functions sought and the account's own votes, saves and ignores.
- **Personal data never goes into prompts outside `public_excerpt`.** The `Privé`
  and `Open vragen` sections are stripped; letters/CVs use only public facts.
- **Accounts are isolated.** Pass ownership explicitly to background work with
  `tenancy.capture`; never rely on module globals for request identity.
- **Sources are config, not code.** New sources go in `seeds/sources.yaml` +
  `data/sources.yaml` with an existing adapter kind; use the `vacaturebron-recon`
  skill and verify content access (not just titles) before enabling.
- **Documents are immutable versions.** Never overwrite generated CVs/letters.
- **Container parity:** the app must work in the Docker image; no browser or local
  model server. The one exception is the pinned static embedding model baked in by
  `docker/fetch_model.py`; without it the app ranks as before. Browser-only sources
  use `local-json` imports instead.

## Delegation (OpenCode agents)

Stock V2 roster only, as in the global config: `build` is the one writer, `plan`
is read-only, `explore` and `general` are the subagents. Every agent runs the
global default model; reasoning effort is chosen per session, not per agent.
There is no `review`, `verifier` or `ux-designer` agent.

- Source/endpoint recon, inventories, "where is X" → `explore`, in parallel when
  the questions are independent.
- Diff review before a commit → `explore` with the frozen HEAD or diff, the
  requirement and the checks already run; it returns findings only. Up to three
  rounds; a round without findings ends the review.
- Acceptance → `scripts/check.sh`, run by the primary on the frozen revision
  (the same gates as CI), plus `scripts/container_smoke.sh` when the image or
  its startup path changed. After two failed correction rounds, change
  diagnosis instead of retrying.
- Cross-family review is optional and manual: a separate session on another
  provider, started by the owner. Nothing in this repository depends on it.
- Implementation and integration stay in the primary; `general` only for a
  bounded unit on disjoint files. Never run parallel writers on overlapping
  files. Compare like-for-like tasks, not a long Build session with a single
  discovery session, when judging delegation cost.
- Project skills: `vacature-analyse`, `cv-brief-standaarden`, `vacaturebron-recon`,
  `career-gui-review`, `system-one-decisions`. They live in `.opencode/skills/`,
  which the app also reads at runtime (`app/standards.py`) and the Docker image
  copies; `.claude/skills` is a symlink to it so Claude Code loads the same
  files. Do not move or duplicate them. Substantial flow/state design uses the
  global `ux-design` skill, read-only.

## Sustained engineering delivery

For an explicitly approved milestone use `/deliver` / `sustained-delivery` and
the project-local 160-step Build pilot. Work up to 60–90 minutes, stopping earlier
on acceptance or a concrete budget/blocker/decision. Reuse the approved phase or
issue list and one checkpoint; completing one task or PR does not require another
`continue`. Give progress commentary and continue ready authorized work.

For meaningful changes freeze the reviewed HEAD/diff while `explore` reviews it
and the check commands run. Inspect complete/partial/blocked results, fix
findings and recheck current bytes. UI increments also use `career-gui-review`;
use the `ux-design` skill for substantial flow/state design, not automatic
restyling.

Commit only owned non-personal files on a feature branch. Push/open PRs when
authorized; native auto-merge requires independent review of current HEAD plus
required green `quality` CI and all branch rules. Never push directly to main,
use admin bypass or accept an empty check list as green. Approval covers no new
spend, remote environment change, privacy exception, release or schema migration.

Background work uses native completion notifications; reconcile jobs after a
reconnect and inspect outputs before acceptance. No background writers or idle
auto-resume watcher. Local servers must have an explicit owner and stop command;
never kill another session's server or mistake a quiet job for failure.
