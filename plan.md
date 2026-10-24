# Jobcraft implementation plan

## Active delivery checkpoint

- Objective: audit the October 19 brief against code, then finish successive ready
  items in phase order without mixing refactors and behavior changes.
- Session: 2026-10-24, started 07:21 UTC; deadline 08:36 UTC (75 minutes).
- Baseline: `main` / `origin/main` at `1ee1dd3`; clean worktree baseline:
  **195 tests passed**, Ruff passed. Docker daemon is unavailable locally.
- Active branch: `phase-3-backup-safety`, in a clean temporary worktree. No personal
  directories, environment files, paid providers or remote services are used.
- Spend cap: $0. Publication, deployments and real-data migrations are prohibited
  by agent policy; owner performs those after reviewing prepared commits.
- Acceptance: focused regressions, full isolated pytest, Ruff, changed JavaScript
  syntax, Docker build when available, independent review and verification of the
  same frozen revision. Do not treat historical green CI as current verification.
- Current action: fix confirmed earlier-phase safety defects before P5.5.

## Branch audit (2026-10-24)

- GitHub reports **no open PRs**. Remote branches are `main` and
  `chore/sustained-delivery` only (fresh fetch with pruning).
- `chore/sustained-delivery` at `a8e8bfc` is patch-equivalent to squash merge
  `5abad7b` (PR #19); all four non-CSV files match the squash result and
  `git cherry main origin/chore/sustained-delivery` marks its commit `-`.
  No unique work needs another merge. Preserve the branch; do not delete it.
- Local `phase-4-match-task` at `6b8c583` is already an ancestor of main.
- Main requires the `quality` check and an up-to-date branch, with admin
  enforcement. Its latest CI run (40071843808) passed at `1ee1dd3`.
- No push, PR creation or merge was performed. These actions are policy-blocked.

## Implementation audit (baseline `1ee1dd3`)

The October 19 brief in the owner's request is the acceptance contract. The old
handoff's "completed" labels overstate completion. Independent discovery reports
were partial and used inconsistent item numbering; the IDs below are reconciled
to the actual brief. This is static evidence plus the baseline 195-test run, not
acceptance of every feature. "Present" means implemented code exists; missing
proof and owner-only gates remain open.

### Phase 0 — present, clean-suite check passed

| Item | Status / evidence | Remaining |
| --- | --- | --- |
| P0.1 | Temporary paths before imports, autouse schema setup, HTTP transport network block, workspace guard in `tests/conftest.py`; baseline suite passed in clean worktree without creating private directories | Guard does not block arbitrary raw sockets or every non-httpx library; existing DB guard misses deletion, WAL-only changes and unrelated private files |
| P0.2 | Feedback tests pass with isolated initialized DB | None found in this slice |
| P0.3 | `tests/factories.py` has job/fake LLM/decision helpers | Some legacy fakes remain duplicated |

### Phase 1 — fixes present, acceptance evidence incomplete

| ID | Code / tests | Remaining |
| --- | --- | --- |
| B1 | Eligible-before-batch and shared exclusion in `matching.match_jobs`, `ranking.excluded_from_matching`; starvation regression in `test_phase1.py` | None identified |
| B2 | Ingest/date normalization, fallback ordering and data fix in `sources.py`, `store.py`; date regressions | Data fix still outside numbered migrations (P3.3) |
| B3 | Raw verdict storage, blend and recovery regression in `test_phase1.py` | Recovery outside numbered migrations; malformed rows are skipped |
| B4 | Public account context in per-request system message; legacy digest test remains | Obsolete compatibility facade still present (P2.6) |
| B5 | Stale filters plus saved/failed-scan exceptions in `store.py`; regression | Full UI/filter acceptance not independently exercised |
| B6 | Per-owner/job/kind locks in `docs.py`; concurrency regression | Template creation is shared per owner and not yet versioned |
| B7 | Token DF, travel, search-text and queue caches; `scripts/bench_vote.py` | Benchmark targets and exact cached/uncached rank parity not proven this session |
| B8 | Open topics injected; plan-file writes removed | Dead path constants remain; explicit fresh-workspace absence test missing |
| B9 | Unknown topic maps to 400; regression | Test calls handler directly rather than authenticated HTTP route |
| B10 | Detail GET no longer clears doc status | Explicit two-GET no-mutation regression not found |
| B11 | Single errors continue; three consecutive failures stop | Existing regressions present; summary/error behavior needs fuller audit |
| B12 | Named dead helpers removed in earlier phase | Dead compatibility/path code still exists elsewhere; do not conflate with named B12 scope |

### Phase 2 — partial, not accepted

| Item | Status / evidence | Remaining |
| --- | --- | --- |
| P2.1 | Direct HTTP client and fake in `llm_client.py` | Requested protocol/accessor/package/error contracts not implemented; `ServerManager` compatibility facade still owns callers |
| P2.2 | Env-configured URL/key/models, timeout/retries, pricing, user agent | Reasoning controls, json_object mode/fallback, cache-read usage, provider-only session header, admin model/error visibility incomplete |
| P2.3 | Plain prompt files and loader exist | Tool-related wording still in prompts; audit gender-neutral wording |
| P2.4 | Pydantic classes declared in `llm_schemas.py` | **Not used by production calls**; no local schema retry/usage ledger contract, consolidation schema missing, no schema fallback |
| P2.5 | UUID sessions, local history and account system context | History kickoff/truncation/cost accumulation acceptance not fully verified |
| P2.6 | Production no longer starts OpenCode child process | `ServerManager`, legacy helpers/fakes and runtime compatibility remain; owner provider smoke test outstanding |

### Phase 3 — foundation only, not accepted

| Item | Status / evidence | Remaining |
| --- | --- | --- |
| P3.1 | `settings.py` frozen dataclass has tests | **Unused at runtime**; module-level `config.py` paths still authoritative |
| P3.2 | `workspace.py` has tests | **Unused at runtime**; legacy layout and `config.user_path` still used |
| P3.3 | Version runner and backup API exist | Baseline migration is no-op; schema/auth/one-off fixes remain outside version chain; original SQL/tree fixture absent; **backup skipped upgrades from v1+ and occurred after mutations** (active fix) |
| P3.4 | State tables and SQLite-first reads exist | File dual-writes/fallbacks remain; message column rename, failed-draft storage and import/export tools incomplete |
| P3.5 | Synthetic file import regression exists | Full two-account historical fixture, byte-offset/history completeness and crash acceptance missing |
| P3.6 | Not implemented | Legacy branches/binaries/jobs columns/bootstrap rename remain; needs reviewed resumable migration, owner-run copy rehearsal |

### Phase 4 — partial, confirmed operational defects

| Item | Status / evidence | Remaining |
| --- | --- | --- |
| P4.1 | Task table, dedupe/index, API primitives in `tasks.py` | **Non-atomic enqueue and SELECT-then-UPDATE claim races**; key-specific `latest` lacks owner check |
| P4.2 | Worker threads, handler registry, stale recovery | No process flock, periodic long-task heartbeat, owner scope centrally enforced or finish-current-task shutdown; real handler integration tests missing |
| P4.3 | Scan/match/consolidate/document/rerank enqueued; task API exists | **Scan handler signature incompatible with worker**; legacy status flag can wedge retry; handlers swallow failures; meta status still UI truth; JSON polling missing |
| P4.4 | Scheduler and pre-round budget check exist | **UTC rather than Amsterdam; earliest instead of latest due slot**; budget not enforced between LLM calls; restart/timing integration proof missing |

### Phase 5 — partial, earlier-phase dependencies incomplete

| Item | Status / evidence | Remaining |
| --- | --- | --- |
| P5.1 | Home/travel form, manual coordinates, word boundaries, 68-place CSV | Built-in Amsterdam fallback/district model remain; national expansion blocked on D6 dataset/licence, not mandatory under brief default |
| P5.2 | Owner source toggles, table, list/matching filters | Regional/national source metadata, 40-km default policy and corresponding tests missing |
| P5.3 | Signal-based hard filters present | Per-owner category questions/neutral taxonomy and effective-question hash not implemented |
| P5.4 | Neutral consolidation/topic text and threshold 3 | Verify remaining skill text and full keyword cleanup |
| P5.5 | Heading aliases present | Still exactly five bullets; numbered bullets, dynamic schema/parser/render and upload preview absent |

### Phase 6 — not started

| Item | Remaining |
| --- | --- |
| P6.1 | Routers/services/web helpers; `main.py` is 1181 lines |
| P6.2 | Authorization dependencies and route inventory guard |
| P6.3 | Scoring package, import-cycle removal and configurable weights |
| P6.4 | Connection closure/busy timeout/FKs, store package/allowlists/orphan migration; `store.py` is 1089 baseline lines |
| P6.5 | Adapter registry, polite robots-aware cached HTTP, known-job detail skip, preserve nonempty fields; dedupe optional |
| P6.6 | B/SIM/RUF rule expansion and public interface type hints; no additional type checker per D8 |

### Phase 7 — not implemented / not accepted

| Item | Remaining |
| --- | --- |
| P7.1 | Explicit proxy trust setting and trusted/untrusted forwarded-IP tests; per-IP limiter |
| P7.2 | Auto secure-cookie mode, security headers/CSP and inline script/style cleanup; keep cookie name |
| P7.3 | Subdomain nginx example without HTML rewriting; no remote beta changes |
| P7.4 | Non-root UID, pinned base/uv, direct venv entrypoint, healthcheck, isolated LibreOffice profiles/no Docker fallback |
| P7.5 | Data-sync guard, predeploy backup, image rollback and retained backup script; remote acceptance owner-only |
| P7.6 | Provider-specific privacy/retention docs and log audit; provider choice remains owner-controlled |

All twelve invariants remain acceptance requirements. The current green suite is
not proof of missing migration, concurrency, paid-attempt accounting or real VPS
acceptance coverage.

## Ordered ready work

1. P3.3 safety slice: back up every existing pending upgrade before schema/data
   changes, fail closed, never overwrite, retain five. Five new regressions first
   failed, then full suite passed (**200 tests**). Review found no blocking defect;
   added WAL snapshot, exact retention and partial-file cleanup coverage:
   **12 migration tests passed**, independently verified; final review found no
   blocking issue. Backup safety slice accepted locally, except Docker gate.
2. P4.1–4.4 safety slices: atomic task enqueue/claim; working scan dispatch/retry
   with durable errors/results; Amsterdam scheduler and latest-slot semantics.
3. P5.5: detect 3–8 styled or numbered CV bullets; preserve heading aliases and
   existing five-point templates; propagate exact count through metadata, prompt,
   schema, parser and renderer. Synthetic fixtures only.
4. P5.5: validate uploads and show detected headings/count before saving.
5. Resolve unmet earlier-phase
   prerequisites before claiming Phase 5 accepted or beginning Phase 6.
6. Phase 6 pure refactors, one sub-phase branch at a time, after prerequisites.
7. Phase 7 hardening; deployment acceptance and real-data checks remain owner-run.

## Known gates

- Docker daemon unavailable: container build/parity not yet verified.
- Owner-only: real-provider smoke test, migration rehearsal on copied real data,
  VPS behavior, provider retention choice, publication and deployment.
- D6: full national gazetteer needs an owner-approved licensed dataset; the brief
  explicitly permits the shipped small CSV plus manual coordinates meanwhile.
- No new dependencies, schema migration execution on real data, source recon or
  external provider calls are authorized by this delivery.
- Backup predicate covers released-version upgrades and missing data-fix markers,
  not manually corrupted current-version schemas with all markers intact. P3.3's
  baseline-schema migration remains necessary. Concurrency during multiprocess
  startup is not accepted until the P4 process lock lands.

## Review and verification receipts

- Audit: `ses_e933656deffeWJkFSrBwaxsnyD` (Phases 0–3, partial coverage),
  `ses_e933656d8ffe3zBuAOOlfs5jnl` (Phases 4–7, partial coverage). Numbering was
  reconciled to the supplied brief above; reports are evidence, not acceptance.
- Backup advice: `ses_e9333fcacffe7o7DaqcBaXRExV`.
- Backup review: `ses_e93309f86ffej5K4yAmCN3UQpN`; verification:
  `ses_e93309f85ffeeOBQnToN68JKbx` (200 tests + five scratch adversarial probes).
- Backup production bytes: SHA-256
  `f6f8cec7a363e81c0af601adf9acfbf043647cfc6dfd0ec335c20701c246f663`.
- Docker build attempted; failed to connect to the absent OrbStack daemon.
- No JavaScript changed in the backup slice; node syntax checks not applicable.
