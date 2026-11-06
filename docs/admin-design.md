# Admin area: design proposal

Status: proposal of 2026-11-06. Step 1 (rail groups, Account split) merged in PR 111; step 2 (Overzicht, Instellingen, usage tables on AI) in PR 113; steps 3 and 4 (Gebruikers, AI grouping, Bronnen) built together on branch `admin-users-ai`. Follows `docs/admin-review.md`
(the findings) and the walkthrough of every admin page on a local copy.

## The problem in one sentence

The admin area is the job-seeker app with the job pages hidden: a rail with
four items under "Instellingen" and no home, a system page that is an AI
usage table with the administrator's own preferences printed under it, and
an Account page that offers a pure administrator a CV upload and a daily
vacancy mail.

## The idea

An administrator asks three questions: does it run, who uses it and safely,
what does it cost. The area is built around those three. One home answers
all three in ten seconds; five pages sit beneath it; the personal page is
reduced to what a person who only administers needs.

## Rail

| group | items | note |
| --- | --- | --- |
| Zoeken | Vandaag, Vacatures, Sollicitaties, Profiel | only when the account has a profile; an owner who is both sees both groups |
| Beheer | Overzicht, Gebruikers, Bronnen, AI, Instellingen | only for administrators |
| Jij | Account, Uitloggen | everyone |

The home link goes to Overzicht for an administrator without a profile and to
Vandaag for everyone else. Today the rail has no group label for the admin
items and lands on Systeem.

## Pages

### Overzicht (replaces Systeem)

Three cards and one list, nothing else.

- **Draait**: worker alive and when last seen, last successful round, tasks
  in the queue. Green when all three are fine.
- **Mensen**: active accounts, sign-ins today, failed attempts, accounts
  without a profile yet.
- **AI vandaag**: tokens against the daily limit, cost today and over
  fourteen days when the gateway reports prices, Jev calls.
- **Aandacht nodig**: failing sources with the reason, failed work with the
  exception type, addresses with many failed sign-ins, the worker when it is
  down. Each line links to the page where it is fixed. When nothing needs
  attention the list says so in one line.

Removed from this page: the full source table (Bronnen has it, better), the
per-kind and per-day usage tables (AI has them), the preferences JSON and the
file paths (Instellingen), the lone "AI-modellen en logboek" button.

### Gebruikers

- One card per account on a phone; the table stays on wider screens.
- Each account: name and username, role, status, last sign-in with address
  and browser, failed attempts, devices signed in.
- Actions behind a small menu per account: nieuw wachtwoord, deactiveren or
  activeren, overal uitloggen. Each asks once, naming the person, before it
  acts. Today one click resets a password and revokes every session.
- "Inlogactiviteit" as a second tab of this page rather than a separate
  link in a subtitle.
- Account toevoegen stays at the bottom; the invitation link stays the
  primary path.

### Bronnen

Mostly right since the recent failure-reason work. Two changes: the daily
schedule moves to Instellingen, and healthy sources show one line (count,
last round) with the detail behind the row, so the failing ones stand out.

### AI (replaces AI-modellen en logboek)

- Model per task grouped by stage: Vacatures lezen en beoordelen,
  Sollicitatie schrijven, Profiel. One Test per row stays, as a quiet link.
- The usage tables from Systeem move here, above the log.
- The log keeps its filters; a call of another account shows the day, not
  the server path.

### Instellingen (new)

Everything that is a setting and nothing that is a status: the scan schedule,
the daily mail (whether SMTP is configured), retention of the AI log and the
sign-in log, the daily token limit and budget as read from the environment,
file paths. Read-only values say so and name the variable.

### Account

For everyone: password, devices signed in with "overal uitloggen", theme,
log out. The CV upload, the daily vacancy mail, the data export and the
account deletion appear only when the account has a profile. An
administrator who also searches keeps all of it.

## What goes

- The preferences JSON and file paths on the system page.
- The duplicate source table on the system page.
- "1 sessie(s)" as a status; it becomes "ingelogd op 1 apparaat" with the
  browser.
- The server path on another account's AI call.
- One-click reset and deactivate without a question.
- Fifteen equal Test buttons in one flat list.

## What gets much better

1. A home that answers the three questions, with an attention list that
   links to the fix.
2. Gebruikers that works on a phone and cannot lock someone out by a slip.
3. A rail that says where you are and who you are.
4. An Account page that fits the person looking at it.

## Order

1. Rail groups and the Account page split: small, and it removes the
   confusion that started this.
2. Overzicht with the three cards and the attention list; Instellingen
   takes what Overzicht drops.
3. Gebruikers: cards, confirmations, menu, the activity tab.
4. AI grouping and the Bronnen polish.

Each step one PR with tests and `scripts/check.sh`, rendered at 800 px and
on a phone before merge.
