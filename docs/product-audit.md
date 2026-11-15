# Product audit: progress and backlog

A running record of the product review that started on 2026-11-12, so the work can
continue after a break. Update it with every batch.

## How the review is done

- The app runs locally on invented data only, never on `data/`. A throwaway data folder
  holds 420 invented vacancies, a profile, votes, saves and verdicts.
- Run it with `APP_ROLE=web` and `SCAN_TIMES=` empty. With the default role the
  scheduler starts and scans the real public sources.
- Every screen is checked at 1280–1440 px and at 375 px, in light and dark. A script in
  the page looks for:
  - controls without a name and inputs without a label;
  - duplicate ids, skipped heading levels and text below WCAG AA contrast;
  - targets smaller than 24 px and horizontal overflow.
- Screens covered: Vandaag, Vacatures, Eén voor één, Vacature toevoegen, vacature
  detail, Sollicitatie (studio), Sollicitaties, Profiel, Account, and the beheer pages
  Overzicht, AI and Bronnen.

## Baseline

The app was already in good shape. The automated checks found, on every screen above:

- no unnamed controls and no unlabelled inputs;
- no contrast failures and no overflow at 375 px;
- one `h1` per page.

The small-target hits were all links inside text, which WCAG 2.2 exempts.

The real problems are in content handling and consistency. They show up only when you
use the screens.

## Done

| # | Prio | Problem (evidence) | Change |
|---|------|--------------------|--------|
| 1 | P1 | Vacancy text lost its structure. "Wat ga je doen?" with a question mark was not recognised as a heading, so headings became loose lines and every list piled up under the last one. This is what people read while the tidy version is made, or when it fails. | `vacancy_text._SPLIT` accepts a `?` after a heading phrase. Tests in `tests/test_vacancy_text.py`. |
| 2 | P0 | The AI page's Test button reported every model as broken. It called the model without a session, which the OpenCode gateway refuses with HTTP 400. Seen live on 2026-11-12. | `_logged_complete` gives every call a session key. Test in `tests/test_reasoning.py`. |
| 3 | P1 | "Zou je hierop reageren?" put Ja left on Vandaag but right on Eén voor één and Snelle start, labelled "Ja, leuk" there. Habit makes people tap the wrong answer, and a vote moves the whole list. | Ja, Nee, Weet ik niet on all three screens. Test in `tests/test_consistent_controls.py`. |
| 4 | P2 | The yes/no card showed the first 320 characters of the page, often a pitch or menu text. | `vacancy_text.summary` uses the section about the work when there is one. |
| 5 | P2 | On every beheer subpage two menu items were marked as current (Overzicht and AI). | `base.html` marks only the deepest matching item. Tested. |
| 6 | P2 | The file field was the browser's grey default button, unlike every other control. | `input[type=file]::file-selector-button` styled like `.button`. |
| 7 | P2 | The salary label rounded up: "tot € 4.100" above a vacancy that pays at most € 4.063. | It rounds down to tens. Tested. |
| 9 | P2 | Within a section, the plain reading showed every paragraph first and every list below them, so "Kom je ook?" stood above the benefits it closes. | `vacancy_text._chunks` records the order and `_vacancy.html` follows it. The AI reading has no order and is unchanged. Tested on the rendered page. |
| 10 | P2 | "Beoordelen gaat vanzelf · 133 in wachtrij" is system language. | "Nog 133 vacatures krijgen een uitgebreide beoordeling", the term the vacancy page uses. Tested. |
| 11 | P3 | The back links on the application and AI-call pages were bare small links. | Same arrow link as the vacancy page. |
| 12 | P1 | Vacatures recounted the review queue inside the request whenever the profile had changed: a routed pass over the whole catalogue, 12.3 s at 25,000 vacancies, run in the 768 MB web process. | The page shows the last count (or nothing) and queues a re-rank for the worker. Tested. |
| 13 | P2 | `count_jobs` made three scans (70 ms), and the dashboard paid for it to learn only whether there are vacancies. | One pass (24 ms, same numbers), and `store.has_jobs()` for the dashboard. |
| 14 | P2 | Security: a hosted site without `APP_PASSWORD` created the legacy account with the known password `dev-local`. | Behind HTTPS it gets a random password in the bootstrap file. Tested. |
| 15 | P2 | Security: Vacature toevoegen checked the resolved address, then let httpx resolve it again (DNS rebinding to 127.0.0.1). | It checks the address the connection actually reached, and ignores proxy settings from the environment. Tested. |
| 16 | P3 | Security: a form without a Content-Length was read whole before the size check. Scraped `javascript:` links were shown. `/docs` listed every route. | The body is read in pieces up to the limit, only http(s) links are shown, and the API pages are off. Tested. |
| 8 | P1 | The web process grew to 640 MB of 768 MB and was OOM-killed (PR #125). | Freed memory goes back after each request (#125), and the 12 s queue recount left the page (#12). A 24-hour memory log of the live web process (2026-11-12 to 11-13) peaked at 133 MB and stayed at 78–80 MB after the deploy; 39 hours later it was 103 MB. Traffic was light, so watch it again after a busy day. |
| 17 | P2 | Sollicitaties scanned the whole catalogue for each of its five status lists. | Index `job_states(owner, status) WHERE status != 'new'` (migration m0009) and a status filter that starts from job_states: five lists 136 → 12 ms at 25,000 vacancies, same rows (PR #129). |
| 18 | P1 | Re-ranking, System One and the LLM step walked only the 20,000 newest live vacancies. With 28,318 live, 2,785 vacancies within reach (posted before 14 October) were never re-ranked or assessed again; 493 of them had an old rank inside the top 1,000. Measured live on 2026-11-14. | The cap is 60,000 and logs a warning when it binds (in the worker: 762 MB peak and a 49 s routing pass at 28,318). The two re-ranks at the end of the swipes run in the worker. The choices step still ranks in the request, limited to the old 20,000 and without the routing pass, and queues the full pass. Tested. |

## Open, in order

1. **P2 list pages.** The duplicate check (`_UNIQUE_JOBS`, window functions over a computed
   key) costs about 110 ms, and /jobs and the dashboard each run it twice. A stored
   duplicate key or deduplicating only the top candidates would fix it. Both change
   behaviour, so they need a design.
2. **P2 onboarding ranks in the request.** After the choices step the swipe picks need
   fresh ranks, so that request still ranks the 20,000 newest vacancies (about 600 MB peak in
   the 768 MB web process). Ranking in the worker with a short wait on the swipe page would
   remove the risk; it changes the flow, so it needs a design.
3. **P2 small wins, not measured.** On Sollicitaties, each card makes 3 separate queries,
   about 180 ms for 107 cards. Today's new matches and the five-for-today round run the
   same query one after the other.
4. **P3 security hardening.**
   - Login throttling is per IP and username only, so it does not stop password
     spraying or rotating IPs.
   - Opening a vacancy page queues AI work on a GET.
   - The shared vector index includes other accounts' private imports. It shows no
     text, but it can influence taste ranking.
   - The `melding` and `fout` parameters show any text a link carries.
   - After a DNS rebind, Vacature toevoegen still sends the GET to the internal address. Only the response is refused. Pinning the checked IP in a custom transport would stop the request too.

   The security review found no access between accounts, no path traversal, no CSRF
   gap, no open redirect and no XSS.
5. **P3 intro of a raw vacancy.** The text before the first heading is shown last, collapsed
   as "Overige tekst van de pagina". This is on purpose, because on scraped pages that text
   is often menu text. Revisit only with real examples where the intro matters.
6. **P3 navigation.** The "Jij" group holds only Account.
7. **P3 answer weight.** On every yes/no card, Ja is the filled primary button and Nee
   is outlined. That may nudge answers. Equal weight is worth trying, but it is a
   product decision.

## Scorecard (1–10, evidence above)

| Dimension | Before | After | Note |
|---|---|---|---|
| Visual design | 8 | 8 | Coherent tokens, light and dark, distinct style; no restyle was needed |
| GUI consistency | 6 | 8 | Vote order, menu state, file field |
| Usability | 7 | 8 | Readable vacancy text, better yes/no context |
| Navigation | 7 | 8 | One current item |
| Workflows | 7 | 7 | Not changed in this round |
| Accessibility | 8 | 8 | Automated checks clean before and after; no screen-reader run |
| Frontend architecture | 7 | 7 | Server-rendered, small JS; not changed |
| Backend architecture | 7 | 7 | Not reviewed in depth this round |
| Database design | — | 7 | Sound schema and owner filters; list pages pay about 110 ms for duplicate detection; status lists indexed (#129) |
| Performance | 6 | 7 | Web memory flat at about 80 MB; 12 s queue recount gone from pages; Sollicitaties lists 136 → 12 ms; list pages still pay for duplicate detection |
| Reliability | 6 | 8 | No restart or error in 39 hours live; Test button fixed |
| Security | — | 8 | No cross-account or injection findings; four hardening fixes done, four listed |
| Maintainability | 7 | 7 | Small, tested changes |
