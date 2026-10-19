# Jobcraft — project instructions

Local-first, multi-account FastAPI app (Jinja + SQLite + `data/`) that runs an
isolated `opencode` child for the LLM parts. Everything personal stays on disk
under `data/` and is git-ignored; never commit `data/`, `seed/`, `.runtime/`,
`.env.local` or generated documents.

## Commands

- Run: `OPENCODE_GO_KEY_FILE="$HOME/.local/share/opencode/auth.json" APP_USER=owner APP_PASSWORD=dev-local uv run uvicorn app.main:app --port 8080`
- Tests: `uv run pytest -q` · Lint: `uv run ruff check .` · Container: `docker build -t jobcraft:local .`
- Quality check after matching changes: `uv run python scripts/matching_quality.py`
  (legacy account) or `--user <name>` for another account.

## Architecture rules

- **Matching is two-stage.** System One (Jev via Runware `/v1/decisions`) produces
  typed signals for every job; only low-confidence/high-rank cases go to the LLM.
  The LLM verdict cache key must **never include feedback** — votes are applied by
  the deterministic ranker (`app/ranking.py`) so a vote costs $0 and stays instant.
- **Personal data never goes into prompts outside `public_excerpt`.** The `Privé`
  and `Open vragen` sections are stripped; letters/CVs use only public facts.
- **Accounts are isolated.** Pass ownership explicitly to background work with
  `tenancy.capture`; never rely on module globals for request identity.
- **Sources are config, not code.** New sources go in `seeds/sources.yaml` +
  `data/sources.yaml` with an existing adapter kind; use the `vacaturebron-recon`
  skill and verify content access (not just titles) before enabling.
- **Documents are immutable versions.** Never overwrite generated CVs/letters.
- **Container parity:** the app must work in the Docker image; no browser or local
  model dependency. Browser-only sources use `local-json` imports instead.

## Delegation (OpenCode agents)

- Source/endpoint recon, inventories, "where is X" → `explore` (~$0.015/session).
- Diff review before a commit → `review` (cross-family, `openai/gpt-6.1-sol`).
- Acceptance checks (tests, pages, PDFs) → `verifier`.
- Implementation and integration stay in the primary; never run parallel writers on
  overlapping files. A build session costs ~50× an explore session — do not do
  read-only recon inline when it can be delegated.
- Project skills: `vacature-analyse`, `cv-brief-standaarden`, `vacaturebron-recon`,
  `career-gui-review`, `system-one-decisions`.
