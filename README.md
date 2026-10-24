# Jobcraft

## Phase 6 code boundaries

`app/main.py` composes the application; `routers/`, `runtime.py`, `workflows.py`
and `web_helpers.py` own HTTP, lifecycle, queued work and presentation respectively.
`web.py` owns the shared renderer. `auth/` separates credentials, sessions, security,
bootstrap and account/admin routes. Every endpoint has an inventoried permission.

`scoring/` contains acyclic rules, signals/travel, cache digest primitives and blend
configuration. `MATCH_WEIGHT_RULE`, `MATCH_WEIGHT_FIT`, `MATCH_WEIGHT_LLM` default
to `0.35`, `0.40`, `0.25`; missing components are renormalized and an all-unavailable
configured blend falls back to rules. Feedback never enters paid verdict keys.

`store/` groups persistence by tables. Connection contexts commit/rollback **and
close**; each enables foreign keys, WAL and a 5000 ms busy timeout. Migration 6
backs up through the existing upgrade path, refuses orphan/cross-owner references
before rebuilding child tables, and never fabricates parents or deletes orphans.
An integrity failure needs owner investigation; no production migration was part
of Phase 6 acceptance.

Adapters are discovered from `app/adapters/`: add one module declaring `KINDS` and
`scan(client, source, entry, summary, limit_per_source=None)` plus configuration.
`JobItem` and transport/adapter protocols live in `adapters/interfaces.py`.
Public scans use cookie-free HTTP with robots checks, host pacing, bounded retries
and conditional caching. Robots 404 permits fetching; denied, redirected or
unavailable policies fail closed. Authenticated clients/requests are unsupported.
Only public responses are cached (64 files, at most 2 MiB each) under the runtime
directory. Known rich job details are skipped for 24 hours from the last real
detail fetch; empty updates cannot erase existing fields.

Ruff includes `B`, `SIM`, `RUF`. Public scoring/store/LLM/task interfaces are hinted;
dynamic provider JSON remains explicit dictionaries. No extra type checker is
required. Synthetic import/size/interface guards run in pytest.

A local-first career workspace for discovering vacancies, keeping a living
profile, ranking opportunities and generating tailored application documents.

Built with FastAPI, Jinja, SQLite and a direct OpenAI-compatible LLM client. The interface
is Dutch; the code and documentation are English.

## Features

- **Accounts** — local username/password login, admin user management, and strict
  separation of profiles, conversations, votes, matches, documents and usage.
- **Shared vacancy catalogue** — sources and vacancies are shared; personal state
  is per account.
- **Interview** — an LLM interviewer maintains a living profile and answer log.
- **Two-stage matching** — cheap typed signals (System One via Runware) for every
  vacancy; LLM verdicts only for promising or uncertain cases.
- **Free re-ranking** — votes adjust the deterministic rank immediately and never
  invalidate an LLM verdict.
- **Application documents** — immutable CV and motivation-letter versions per job.
- **Travel estimates** — e-bike/public-transport estimates from the configured
  home base; unknown locations are not vetoed.

## Quick start

Requirements: Python 3.12+, `uv`, and an OpenAI-compatible LLM endpoint.

```sh
uv sync
cp .env.example .env.local
# edit .env.local: provider key, APP_USER, APP_PASSWORD
set -a; source .env.local; set +a
uv run uvicorn app.main:app --reload --port 8080
```

Open <http://127.0.0.1:8080/login>. On first startup the app creates the first
account from `APP_USER` / `APP_PASSWORD`, plus a local `admin` account. Their
generated passwords are written once to `.runtime/bootstrap-accounts.json`
(mode 0600, git-ignored). Change passwords through **Mijn account** afterwards.

`APP_USER` / `APP_PASSWORD` only seed the first account. They are not consulted
for later logins. In an existing workspace, keep the current `APP_USER` value:
changing it renames the legacy account (its password is unchanged); other
accounts are unaffected.

### Worker lifecycle

Run **one uvicorn process/worker per data directory**. Startup holds an exclusive
`data/.jobcraft.lock` before database setup; a second instance exits with a clear
error. Do not delete this lock file to bypass a running instance.

`TASK_WORKERS` (default `2`) controls the in-process task worker threads, not
uvicorn workers. Tasks restore their persisted owner's scope; inactive or missing
owners fail closed. Active tasks receive a heartbeat every 30 seconds. After a
restart, tasks left running are marked interrupted so they can be retried.

Shutdown stops scheduling and waits for current handlers before closing the LLM
client and releasing the process lock. Allow enough termination grace for long
provider calls; an external forced kill can still interrupt work. Only queued
tasks can be cancelled; running handlers finish before their concurrency slot is
released. This queue does not promise exactly-once external side effects after a
crash; inspect immutable document versions before retrying interrupted work.

## Data and privacy

Everything personal stays on disk and is git-ignored:

```
data/
  app.db                 SQLite database (accounts, sessions, jobs, matches, usage)
  profile/               legacy profile, preferences, answer log, history
  users/<id>/            per-account profile, jobs, templates and seed files
  templates/             CV template + identity derived from the account's CV
  sources.yaml           shared source configuration
seed/                    optional local bootstrap material (never committed)
.runtime/                local runtime logs and one-time account bootstrap
```

Never commit `data/`, `seed/`, `.runtime/` or environment files. The repository
also ignores databases, generated documents and common secret files.

Personal data is stripped before prompts: only the public profile excerpt is
sent for matching and document generation. The `Privé` and `Open vragen`
sections are not used in applications.

## Accounts

- New accounts start empty: no profile, preferences, votes, matches or documents.
- A new account can upload its own DOCX base CV through **Mijn account**.
- Administrators manage accounts and shared sources; admin status does **not**
  grant access to another account's private content.
- There is no public registration and no workspace-reset endpoint.

## Matching

1. **System One gate** answers typed questions per vacancy and caches signals.
2. **Deterministic ranker** blends rule score, signals and a fresh LLM verdict.
3. **LLM escalation** only for unvoted, unprocessed vacancies above the rank
   threshold with uncertain signals.

The verdict cache key covers preferences, the public profile and a prompt
version; feedback and `preferences.updated_at` are excluded so voting stays free.

Quality check:

```sh
uv run python scripts/matching_quality.py              # legacy account
uv run python scripts/matching_quality.py --user name  # another account
```

## Sources

Sources are configuration, not code. Active adapters and examples live in
`seeds/sources.yaml`; a copy is used at `data/sources.yaml`. Admin-only controls
on `/sources` scan or toggle the shared catalogue. Browser-only sources use
`local-json` imports so the container stays browser-free.

## Documents

Per job and account, generated CVs and letters live under `data/jobs/` or
`data/users/<id>/jobs/`. Every generation creates a new immutable version
(`kind-vN.docx` + PDF). An interrupted run never overwrites an existing version.

PDF conversion uses LibreOffice; on macOS without it, the app can use the local
Docker image as converter.

## Docker

```sh
cp .env.example .env.local   # fill provider keys and the first account
docker compose up --build
```

The app listens on `127.0.0.1:18080` and keeps data in `./data`, seed material in
`./seed`. The container includes LibreOffice; LLM calls use the configured API endpoint.

## Deploying

`scripts/deploy_vps.sh` uploads code (or data with `SYNC_DATA=1`) to a Docker
host. Environment files are never uploaded; the remote `.env.local` is required
and stays `0600`. A failed image build aborts before the container is recreated.

For a path-prefixed reverse proxy, adapt `deploy/nginx.conf.example` and
`deploy/compose.vps.yml`. Set `VPS_HOST` (and optionally `VPS_DIR`,
`PROXY_NETWORK`) rather than hardcoding host details.

## Tests

```sh
uv run pytest -q
uv run ruff check .
node --check app/static/job-drawer.js
```

Tests use temporary databases and never call paid providers. Personal data and
the working database are not part of the repository or its history.

## Project layout

```
app/            FastAPI routes, auth/tenancy, matching, documents, storage
prompts/        consolidation prompt
.opencode/      project skills (document, vacancy, source and UI standards)
seeds/          source catalogue template
scripts/        deploy, key loading and matching-quality tools
deploy/         optional reverse-proxy/compose examples
tests/          isolated unit and route tests
docs/           design and verification records
```
