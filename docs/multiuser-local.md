# Local multi-user foundation

Approved: shared public vacancy/source catalogue; private profiles, conversations,
votes, matching state, usage and documents. Accounts: existing The legacy account remains a
user with unchanged partial bootstrap/credentials, new admin, clean test-user.
No public signup, workspace reset or remote deployment.

Implementation: additive ownership migration in SQLite, user job-state overlay,
request-local immutable principal and owner-captured background tasks. Existing
Legacy file paths stay in place; new accounts use data/users/<opaque-id>. No
global path/environment switching. Shared AI service denies filesystem tools;
the application injects owner-scoped interview context and authorizes sessions.

Safety: snapshot before migration; compare original legacy file hashes and
database values after migration; idempotent migrations; cross-account route,
cache, document and concurrency tests before enabling local accounts.

## Local access and operations

- Open `http://127.0.0.1:8080/login`. The legacy account's existing local username/password
  remain valid; The legacy account is a normal user, not an administrator.
- New `admin` and `test-user` passwords are generated once in the git-ignored
  `.runtime/bootstrap-accounts.json` (0600). Each plaintext password is cleared
  from this handover file after its first successful login. Subsequent password
  changes happen via Account or an administrator's reset action.
- The account menu exposes Account, password change and logout; administrators
  additionally see Users and technical insight. Only admins change shared source
  configuration or start a shared scan. Admin privileges do not bypass ownership.
- Fresh users onboard via their own interview and explicitly accept a profile
  proposal. No automatic bootstrap, reranking, seed copy or reset. Their own CV
  can be uploaded via Account (macro-free DOCX, 5 MB maximum; current template
  requires PROFIEL and STERKE PUNTEN with five bullet points).
- Authentication uses salted scrypt hashes, opaque revocable/expiring session
  cookies (HttpOnly, SameSite=Lax) and session-bound CSRF tokens. Dynamic pages
  use `Cache-Control: no-store`. Use `APP_COOKIE_SECURE=1` when later hosting
  behind HTTPS; no remote deployment is part of this change.
- Existing maintenance scripts without an explicit account scope still address
  the legacy account. New user tasks must capture ownership with `tenancy.capture`.
  Sessions have globally unique upstream UUIDs; document versions are owner-scoped.
  `scripts/matching_quality.py --user <name>` checks one account explicitly.

## Audit hardening

- Admin deactivation is atomic (`BEGIN IMMEDIATE`): two simultaneous requests
  can never remove the last active administrator.
- Verdict caching hashes the public profile excerpt and a prompt version in
  addition to preferences. Feedback stays outside the key, so voting remains free.
- Provider usage is recorded before response parsing, so malformed AI answers
  still appear in cost accounting.
- Document versions consider registered rows and orphan files; an interrupted
  run can never overwrite an existing CV or letter.
- Deployment never uploads `.env*` files and aborts before container recreation
  when the image build fails.

## Verification

- Local pre-migration snapshot: `.runtime/backups/before-multiuser-<timestamp>/`;
  `.runtime/multiuser-backup-path.txt` identifies the snapshot. All original
  values in nine tables and all 25 original data/seed files were fingerprinted
  and verified unchanged after migration and app restart.
- Pytest: 153 tests pass; Ruff, JavaScript syntax and diff whitespace checks pass.
  The existing Starlette/httpx test-client deprecation warning remains.
- Jobs UI fixture (synthetic only): colour-coded scores with confidence, compact
  positive/negative signal chips above the filters, drawer focus restore, and no
  horizontal overflow at 1440/820/390px.
- Independent verifier also exercised actual pre-ownership and older schemas
  using synthetic SQLite databases: 66 migration and 34 isolation challenges
  passed. Legacy IDs, original values, owner overlays and idempotency preserved.
- Chromium fixture checks: independent account logins, fresh-user onboarding,
  chat, profile proposal acceptance, per-user votes/status/drawer, admin account
  creation, member denial on admin/foreign-ID routes, mobile account/logout and
  unchanged legacy profile/partial-bootstrap state. Browser AI used a fake
  manager; no paid provider calls or real profile edits were made.
- The real local app accepts The legacy account's existing credentials, rejects member admin
  access, and revokes access after logout. New accounts have zero private records.
- Live LLM responses and PDF conversion were not exercised; remote app/radio
  were not accessed or changed. This is a local-development implementation.
