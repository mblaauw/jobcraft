# Jobcraft implementation plan

## Active delivery checkpoint

- **Current milestone: whole P6**, authorized 2026-10-24. Start 09:51 UTC;
  delivery window through 11:21 UTC (90 minutes). Branch `phase-6-web-structure`
  from `6f61220`. Fresh baseline: 305 tests passed and Ruff passed.
- Execute P6.1 web/runtime/routers, P6.2 authorization dependencies/inventory,
  P6.3 scoring package/cycle removal/configurable weights, P6.4 store connections,
  grouping/allowlists/FK preflight, P6.5 adapter registry then separately reviewed
  HTTP/ingestion improvements, P6.6 lint/type hints and size/import acceptance.
- Publication authorized: feature branches, independent frozen review/verifier,
  required current green quality CI and protected PR merges; never bypass rules.
  Spend cap $0; no private directories/environment files, paid providers, production
  migration/deployment or remote environment changes. Synthetic SQLite only.
- Preserve route and document contracts, account isolation, public prompt projection,
  usage accounting, immutable documents and feedback-free paid caches. P7 and
  earlier-phase behavior gaps remain excluded. No additional type checker (D8).
- Done: P6.1 merged #31 (`a771929`), 307 tests + three independent AST probes;
  P6.2 merged #32 (`6dcfa90`), 308 tests, full 43-endpoint dependency inventory,
  auth modules <=305 lines. Both independently reviewed, Docker/Ruff and required
  current quality CI passed; no publication bypass.
- P6.3 merged #33 (`88e583c`): 317 tests, independent scoring parity probes;
  missing compatibility export and large-finite-weight overflow findings resolved.
- P6.4a merged #34 (`22deb27`): 74 function/module-assignment AST parity checks,
  317 tests; table-group extraction separate from integrity behavior.
- P6.4b merged #35 (`8c5ff5f`): 323 tests and 14 independent integrity probes;
  connection closure (including commit failure), busy timeout, allowlists and
  orphan-checked transactional foreign keys, sequence/index/ID preservation.
- P6.5a merged #36 (`bdcff10`): 327 tests, 27 helper ASTs plus inline WP parity;
  registry, job/adapter interfaces and one-file adapter discovery.
- P6.5b merged #37 (`7c1e7f7`): 341 tests plus 107 adversarial HTTP/recency probes.
  All confirmed findings resolved: Rijk detail timestamps, gzip 304 metadata and
  malformed cache mappings. Public cookie-free HTTP, robots checks, bounded
  retries/pacing/conditional cache, nonempty merge and nonrolling detail recency.
- P6.6 final source frozen at tree `bfd94216e47f5a79c0b302f22f07e12609753081`:
  public interface annotations; shared prompt-version authority; B/SIM/RUF lint;
  pure CV-template/conversion and preference-model extraction. All application
  Python files <=400 lines (largest 393), main <100; fresh-process imports and
  public annotation guards added. Primary full suite: 344 passed; strict Ruff,
  all three JavaScript syntax checks and Docker image `a2019b69` passed.
- Matching quality command exercised with 100 synthetic scored vacancies: all
  top-N overlaps 100%, rank/fit correlations 1.000. This is command/fixture
  correctness, **not** evidence of real-world matching quality; no private data
  or provider calls were used. Final independent verification completed: **344
  tests**, **72 focused acceptance tests**, strict Ruff passed on the exact frozen
  source. No unresolved findings. Container smoke check also passed with networking
  disabled: imports, renderer singleton, all ten adapter kinds and synthetic schema
  version 6/FK enforcement. P6.1–P6.6 accepted; final publication remains gated on
  current quality CI and protected merge (recorded in the final PR receipt).
- Final static review `ses_e926211b9ffeAGbc3bcAIB6xoq`: complete, all 37 changed
  files inspected, no material findings. Final verifier
  `ses_e9261e3ddffeVgB49OCRVVz0M1` completed on the same frozen source tree.
- All earlier P6 PRs passed independent review/verifier, Docker and required
  quality checks before merge. No production migration/deployment or new spend.
- Read-only architecture advice `ses_e92a7849fffefBfUy5eqSOxT31` and adapter
  discovery `ses_e92a71d1cffeanQP2vtCC2t35P` inspected. Implementation stays primary.

### Completed P5 checkpoint

- **Current approved milestone (08:12 UTC, 2026-10-24):** commit, push and merge
  the prepared stack, then complete all P5.1–P5.5. Deadline 09:42 UTC (90 minutes).
- Publication completed: #26 `3c210ef`, #27 `add36e1`, #28 `fc7dfac`, #29
  `95054f0`; required quality green for each current merge head. Each snapshot
  Docker build passed. Latest independent verification: 233 tests, Ruff and real
  blocked-tick/flock probe passed; primary checked all three JavaScript files.
- Current branch: `phase-5-per-user-domain`, based on `origin/main` / `95054f0`.
- User authorized GPT-6.1 Sol read-only subagents after Go quota failure. OrbStack
  was started successfully. Prior provider/Docker/publication blockers below are
  historical, not current authority or blockers.
- Authorized: source/config changes and synthetic checks for all Phase 5, commit,
  push, PR and protected merge. No paid app-provider calls, production deployment,
  real-data migration, personal-directory/environment-file access or new packages.
- D6 default: ship the existing 68 places as authoritative CSV plus manual
  coordinates; no national dataset acquisition required. D7: propose a neutral
  roughly ten-category YAML catalogue. Existing scoring weights remain unchanged.
- Implementation order: P5.1 neutral travel/cache correctness; P5.2 shared regional
  metadata and coherent per-owner defaults; P5.3/P5.4 effective question snapshots,
  public-preference projection and neutral text; P5.5 exact-count CV contracts and
  owner/session-bound inspect-confirm upload. One work item per commit, one P5 PR.
- Acceptance: all requested regressions, full isolated pytest, Ruff, all changed
  JS syntax, Docker build, independent frozen-revision review and verification,
  synthetic rendered DOCX/PDF and upload/source UI evidence, required green CI.
- **P5.1–P5.5 implemented and accepted under the brief's defaults:** authoritative 68-place
  catalogue plus district data; neutral defaults and correct home-snapshot cache;
  coherent regional/owner source policy; per-account category snapshots and
  private-preference projection; common neutral standards; exact 3–8 CV contracts,
  owner/session-bound inspect-confirm flow and conservative DOCX-resource policy.
- Final source commit: `0f122623b50e9c25ecff23750c326e1e011216f6`; tracked tree
  `18579e38e3cfc6fd8a83ead0cdb9260b64a3246b`, identical to independently reviewed
  and verified source. Five work-item commits plus one confirmed-review-fix commit.
- Final checks: **305 tests passed**, Ruff clean, all three JS syntax checks
  passed, Docker image `b6258d64` built successfully. No dependencies or schema
  migrations added. Publication: **PR #30**, protected merge requires green quality
  on the final documentation-inclusive head; no bypass or direct push to main.
- Independent review: domain `ses_e92d23268ffezxfQGYTA1pqZxy`, CV/upload
  `ses_e92d23259ffe4Xk06l3zJddsUc`; independent verifier
  `ses_e92d23257ffekwv9w7bowUMmgl` repeated full305, Ruff and 18 adversarial probes.
  All confirmed findings resolved: consolidation privacy/local preservation,
  matcher cache version, interview boundaries, original JSON array validation,
  encoding-independent DTD rejection, template snapshot regression and scoped
  employer-transport negation (advisor `ses_e92bbada8ffe9CrLPosggmXXVQ`).
- Synthetic PDF evidence: numbered 3/5/8-point CVs each rendered as **one A4 page**
  inside Docker. Browser: eight-point preview preceded canonical save; confirmation
  succeeded and removed the upload input; Eindhoven regional-source defaults off.
  Narrow rendered frame (~375 px) had no horizontal overflow, focusable 44 px-high
  confirmation button. Screenshot/contrast inspection was unavailable (tab not
  visible to screenshot tool); no pixel-perfect claim. Owned UI server stopped.
- Remaining after P5: full P2 structured-client contract, P3 settings/workspace and
  legacy migration, P4 residual UI/results/budget/coalescing work, then P6/P7 as
  listed in the historical audit. D6 national expansion, legacy CV replacement,
  general Dutch NLP and LibreOffice sandboxing are not part of this P5 default.
- Architecture advice `ses_e92fe5c3bffen1L83taQNLZnf9`
  and upload UX specification `ses_e92fe5c35ffeNqBYj0dnEvFAPj` inspected; no source
  work delegated. Car ownership is kept distinct from employer-provided transport;
  no invented driving budget or automatic travel veto bypass.
- Upload security advice `ses_e92de52a6ffewxf0TMtpSBmrPe`: DOCX policy rejects
  external loaded resources, active objects/fields and ambiguous packages while
  allowing ordinary web/mail hyperlinks. This is **not** a LibreOffice sandbox;
  general converter confinement remains P7. Existing unsafe templates fail closed.
- Domain-code grep has no region/category/taste literals left; remaining
  `Europe/Amsterdam` references are scheduler/display timezones required by P4,
  not home-region defaults. The brief's literal grep must account for this conflict.

### Historical delivery checkpoint (through 08:03 UTC)

- Objective: audit the October 19 brief against code, then finish successive ready
  items in phase order without mixing refactors and behavior changes.
- Session: 2026-10-24, started 07:21 UTC; deadline 08:36 UTC (75 minutes).
- Baseline: `main` / `origin/main` at `1ee1dd3`; clean worktree baseline:
  **195 tests passed**, Ruff passed. Docker daemon is unavailable locally.
- Active branch: `phase-4-scheduler-shutdown`, stacked on reviewed lifecycle work,
  in a clean
  temporary worktree. No personal
  directories, environment files, paid providers or remote services are used.
- Spend cap: $0. Publication, deployments and real-data migrations are prohibited
  by agent policy; owner performs those after reviewing prepared commits.
- Acceptance: focused regressions, full isolated pytest, Ruff, changed JavaScript
  syntax, Docker build when available, independent review and verification of the
  same frozen revision. Do not treat historical green CI as current verification.
- Current action: fix confirmed earlier-phase safety defects before P5.5.
- Audit checkpoint commit: `d22520f`; backup safety commit: `9fae7f7` on
  `phase-3-backup-safety` (unpublished).
- Queue safety branch `phase-4-queue-safety` ends at `1538c27`, containing
  `0183121` (atomic/owner-safe queue), `890d9a3` (scan dispatch), `a941eb5`
  (Amsterdam scheduler). These branches are stacked, not merged into main.
- Lifecycle branch `phase-4-worker-lifecycle` ends at `29934d9`, with code commit
  `3e314d9`. Main remains unchanged at `1ee1dd3`.

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

### Phase 6 — completed (baseline audit retained below)

All six items are now implemented and accepted; see the current delivery checkpoint
and separate PRs #31–#38. The table below records the original gaps, not open work.

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
   Implemented regressions failed first; frozen revision now has **218 tests
   passed**, Ruff and all three JavaScript syntax checks passed. Independent
   stress verification confirmed atomic claims/dedupe. Verification found a
   cross-midnight scheduler omission; fixed with a regression. Review requested
   stronger race synchronization and nonvacuous no-fanout coverage; both added.
   **24 affected tests passed**, independently verified; verifier full suite:
   **219 passed**, Ruff clean, seven adversarial probes passed. Correction review
   found no blocking issues. Safety slices accepted locally except Docker gate.
   Quick-restart orphan recovery remains a separate P4.2 lifecycle item.
   Lifecycle implementation now frozen for review: exclusive process lock before
   initialization; central owner scope and inactive-owner rejection; recent
   orphan recovery; periodic heartbeat; client-before-workers startup and
   finish-current-task shutdown; safe queued-only cancellation and terminal
   transition guards. Thirteen regression cases pass; full suite **232 passed**,
   Ruff clean, independently repeated. Static review completed with no blocking
   findings; six adversarial lifecycle probes passed, including second-process
   flock and recovery after a real six-second SQLite write lock. No publication
   or deployment. Remaining lifecycle edge cases are listed below.
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
- Phase 4 review: `ses_e9326ac68ffeFTjfKJGB1Timtp`; verification:
  `ses_e9326ac63fferYGsYOBBxWCo8P` (complete, including correction round).
- Synthetic 2000-job benchmark (no providers): feedback rebuild 215 ms, full
  rerank 2203 ms, cached queue depth 0 ms, single-job rerank 1 ms. These are
  component timings, **not** proof of the brief's HTTP latency targets. Vote
  still calls feedback rebuild synchronously; move it into the rerank task.
- Scheduler catch-up is intentional: an empty last-fire marker causes the latest
  prior slot to fire on first startup, including yesterday before today's first
  slot. Scheduled scanning remains provider-free; configured match-after-scan may
  enqueue paid work at runtime (never enabled against providers in these checks).
- Lifecycle advice: `ses_e93238395ffeaRi8eQCwLeUBMc`; review:
  `ses_e931a3845ffez34w7vVrYaxJkp` (partial static report), completed independent
  static review `ses_e9316ec48ffeWNKQXt6TlLSAE3`; verification:
  `ses_e931a3840ffefDijkH9TXlTYkY` (complete; source hashes checked unchanged).
- Lifecycle scope is Unix/macOS/Linux only (`fcntl`), matching the supported host
  and Docker platform. Forced container termination may still interrupt tasks;
  deployment grace is an owner-controlled deployment setting, not changed here.
- Matching quality script ran successfully on three synthetic scored jobs with
  rank correlation 1.000. This verifies script execution, not real match quality.

## Current blockers and exact continuation

- At 08:00 UTC the next advisor dispatch (`ses_e93135952ffe2zBWGV8KSz3xWy`)
  failed with **"Go usage limit exceeded"**. This is a subagent-provider quota
  blocker, not exhaustion of primary tools. Do not bypass it or claim an
  unreviewed implementation accepted. Existing lifecycle review/verification
  completed; no new architectural work has started.
- Next ready P4.3 item: move feedback rebuilding to the rerank task, keeping vote
  persistence/single-job rerank instant and $0. Before doing so, resolve durable
  coalescing: a vote arriving while rerank is running currently dedupes to the
  active task and can be missed after its feedback snapshot. Proposed payload
  revision + atomic requeue-on-finish needs advisor review; do not implement a
  naive asynchronous move that loses votes.
- Then enforce the daily budget between LLM escalations (not only at round start),
  propagate document/match/consolidation failures/results into durable task rows,
  and replace meta status/UI polling with tasks.
- Scheduler shutdown follow-up is now implemented in its own branch: removed the
  five-second timed join and clear the completed thread reference. A new failing
  regression reproduced the old timed join before the fix. Final local checks:
  **233 tests passed**, Ruff passed, all three JavaScript syntax checks passed,
  diff whitespace check passed. **Independent review/verification of this final
  two-file follow-up remains pending because of the provider quota blocker.**
  Do not conflate earlier lifecycle reviews with these newer bytes.
- Other lifecycle edges remain: a DB failure in
  both terminal update and error recording may leave a running row until restart;
  a worker may claim one last task in the stop-check race; unbounded handler join
  requires deployment grace. Real resolver integration coverage can be stronger.
- Docker remains unavailable, so **no branch satisfies the complete PR definition
  of done yet**. No phase is being presented as wholly finished.

## Final evidence / handoff

Status: **partial / blocked**, stopped for subagent-provider quota and unavailable
Docker, not a claimed primary runtime limit. No pending background writers or
servers. Child sessions finished; scratch probes remain untracked and are not
part of any commit.

Prepared branch stack (publish/review in this dependency order, owner only):

| Branch | Base | Work IDs / acceptance |
| --- | --- | --- |
| `phase-3-backup-safety` | `main` (`1ee1dd3`) | Audit + P3.3 backup safety; reviewed and independently verified; Docker gate open |
| `phase-4-queue-safety` | `phase-3-backup-safety` | P4.1/P4.3/P4.4 safety slices; reviewed and verified after corrections; Docker gate open |
| `phase-4-worker-lifecycle` | `phase-4-queue-safety` | P4.2/I2/I3 lifecycle safety; static review complete, verifier full232 + six probes; documented residuals and Docker gate open |
| `phase-4-scheduler-shutdown` | `phase-4-worker-lifecycle` | Final scheduler stop safety; local233/Ruff/JS pass; independent review and Docker pending |

Final follow-up bytes:
- `app/scheduler.py` SHA-256
  `b36d23ff45930a0e185bf83a4ee92d005aa5afb35850df88d28d2c14039b3ea5`.
- `tests/test_scheduler.py` SHA-256
  `aa6c3076084415cb393703a3bf9c28f940e91961b68d36240c80c106268fba9e`.

No dependencies added; no real-data migration, destructive cleanup, provider
smoke call, remote environment change, push, PR creation, merge or deploy was
performed. Primary workspace is the clean temporary worktree
`/private/var/folders/9r/1qfn68ds57jd6hcmhn32j47c0000gn/T/opencode/jobcraft-cv-bullets`;
the original `/Users/mich/dev/jobcraft` stays on unchanged `main`.

Resume: review the final scheduler follow-up, run Docker when the owner makes its
daemon available, then obtain advisor input on durable rerank coalescing before
moving feedback rebuilding out of the vote route. Continue the unmet earlier
phase contracts listed above; do not skip directly to Phase 6 or claim P5 done.
