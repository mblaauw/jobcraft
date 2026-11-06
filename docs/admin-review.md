# Admin review and plan

Status: review done on 2026-11-06; step 1 (the login log) approved and built
the same day on branch `login-log`. Steps 2 to 4 wait for approval.

Walked as a test administrator on a private copy of the local data, at 800 px
and on a 375 px phone: Systeem (`/admin`), Gebruikers (`/users`), AI-modellen
en logboek (`/admin/ai` and one call), Bronnen (`/sources`, admin view),
Account, and the invitation flow. Checklist: `career-gui-review`.

## What is good

- One stylesheet, one set of components; the admin pages look like the rest.
- Every admin page says what it will not show: no profiles, conversations or
  documents of others. The AI log keeps other people's prompts off the page.
- Sources carry real health: failing rounds, empty rounds, share with full
  text, history of the last rounds, a warning banner naming the sources that
  need attention.
- Invitations are one-time, seven days, and the link is shown once.
- Focus styles, labels and table headings are in place everywhere.

## Findings

`bestand:regel — P — probleem — fix`

### P1

1. `app/templates/users.html:33` — the status chip wraps inside the word
   ("actie / f") at 800 px and on a phone, and the five-column table pushes
   the actions off-screen on a phone. Fix: below 760 px render one card per
   account (name, role, status, last login, actions) instead of a table; give
   chips `white-space: nowrap`.
2. `app/templates/users.html:40` — "Nieuw wachtwoord" resets a password and
   revokes every session with one click, no confirmation. "Deactiveren" too.
   One slip locks a person out. Fix: a confirm step in place, the way document
   removal does it (`details` with the button inside), with the person's name
   in the question.
3. `app/routers/pages.py:87` — "Laatste geslaagde ophaalronde —" on Systeem
   while Bronnen shows rounds fetched today. `_last_scan` reads `last_ok`
   from `source_health`, which the current scan does not write for every
   path. Fix: derive it from the sources' `last_scan_at` where
   `last_status == ok`.
4. `app/templates/admin.html:49` — Systeem repeats the whole source table
   (28 rows) with less information than Bronnen, below the fold. Fix: one
   line (active, failing, last round) with a link, and only the failing
   sources listed.
5. `app/templates/admin.html:59` — "Bestanden en ruwe voorkeuren" prints the
   administrator's own preferences JSON, home coordinates included, on the
   system page. Not a system matter. Fix: drop the preferences; keep the file
   paths in the disclosure.
6. `app/templates/admin.html:39` — "Mislukt werk" shows a kind and a count and
   sends the administrator to a worker log they cannot reach from the UI.
   `tasks.error` is stored. Fix: show the last error per kind, exception type
   only, as the match summary already does.
7. Nowhere shows who logged in when and from where. The users page shows
   "Laatst ingelogd" and "1 sessie(s)". See the login log below.

### P2

8. `app/templates/admin_ai.html:14` — fifteen model rows, each with its own
   Test button, in one flat list. Fix: group by stage (documenten, matching,
   profiel) with a heading per group.
9. `app/templates/admin_ai_call.html:19` — a call of another account shows
   the absolute server path of the log file. Fix: show the day only.
10. `app/templates/users.html:33` — "1 sessie(s)" says nothing a person can
    act on. Fix: "ingelogd op 1 apparaat" with the browser and the IP once the
    log exists, and a "Overal uitloggen" action per account.
11. `app/templates/sources.html:62` — the primary button and the banner both
    say a round is running; the button could show the start time instead.
12. `app/templates/base.html:21` — an administrator without a profile sees
    only "Instellingen" in the rail and lands on Systeem; an owner who is also
    a job seeker gets no hint which hat they wear. Fix: a small "Beheer" label
    in the rail group title when the account is an administrator, and the
    home link stays Vandaag when a profile exists.

### Behind the proxy

`Dockerfile:47` starts uvicorn without `--proxy-headers`, so on the server
`request.client.host` is the nginx container's address for everyone. The login
rate limit in `app/auth/rate_limit.py:46` keys on it, so all visitors share
one failure budget today. The nginx example already sends `X-Forwarded-For`.
Any login log needs this fixed first, or every line says the same address.

## The login log

Purpose: an administrator can see who logged in, when, from which address and
browser, and which attempts failed. Security only; nothing about what people
do inside the app.

### Data

New table `auth_events`:

| column | |
| --- | --- |
| `id`, `at` | when |
| `kind` | `login_ok`, `login_failed`, `logout`, `invite_used`, `password_changed`, `password_reset`, `deactivated`, `activated`, `sessions_revoked` |
| `user_id` | the account, null for a failed attempt on an unknown name |
| `username` | as typed, for failed attempts only, so a typo is visible without naming a real account |
| `ip`, `user_agent` | from the request, agent cut to 200 characters |
| `actor_id` | the administrator who did it, for the admin actions |

Written from `login_submit`, `logout`, the invitation acceptance, the password
and user routes. Not written by the middleware: `auth_sessions.last_seen_at`
already says when a session was last active. Retention `AUTH_LOG_DAYS`
(default 90), pruned by the daily scheduler; an account's own events go into
its export and disappear with the account.

The real address: run uvicorn with `--proxy-headers` and
`FORWARDED_ALLOW_IPS` set to the proxy network in `deploy/compose.vps.yml`,
so `request.client.host` is the visitor's address and the rate limiter keys
on it too. Locally nothing changes.

### Pages

- `/users`: per account "Laatst ingelogd 06-11-2026 09:39 vanaf 51.x.x.x,
  Safari op Mac", and the number of failed attempts on that name in the last
  day when there were any.
- `/users/activiteit`: one table, newest first, filters on account, kind and
  period (dag, week, maand). Columns: moment, account, gebeurtenis, adres,
  browser. Failed attempts on unknown names show the typed name in grey.
  Above the table: failed attempts in the last 24 hours grouped by address.
- `/admin`: one line "Inloggen: 3 geslaagd, 1 mislukt vandaag" linking to the
  activity page, replacing nothing.
- Account page, "Jouw gegevens": one sentence that login moments, address and
  browser are kept for 90 days for security and are in the export.

### Tests

An event per path, the typed name only on failures, the proxy address
parsing, pruning, both pages rendering, the permission inventory, and that a
deleted account leaves no events.

## Plan

1. **Login log.** The table and its migration, the events, the proxy headers,
   the two pages and the account sentence. This is the schema migration and
   the remote-environment change (compose) that the project rules say need
   explicit approval.
2. **Gebruikers.** Cards on a phone, confirmation on reset and deactivate,
   "Overal uitloggen", the session wording.
3. **Systeem.** The last-round fix, the slim source summary, the failed-work
   detail, no preferences dump, the login line.
4. **The P2 rest.** Grouped model rows, no server path on a call, the rail
   label.

Each step one PR with tests and `scripts/check.sh`; the users and system
pages also pass `career-gui-review` before merge.
