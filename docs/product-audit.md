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
| 8 | P1 | The web process grew to 640 MB of 768 MB and was OOM-killed (PR #125, merged). | Freed memory goes back after each request. The root cause of the remaining growth is still open (below). |

## Open, in order

1. **P1 web memory.** The lab levels off near 300 MB, the live process reached 640 MB.
   A memory log of the live web process runs until 2026-11-13 ~17:20 UTC in
   `~/jc_memlab` on the VPS. Line its jumps up with the access log, then remove the folder.
2. **P2 raw vacancy text order.** The intro before the first heading is shown last, as
   "Overige tekst van de pagina". Inside one section, paragraphs and lists are shown
   apart: all paragraphs, then all items. Keeping the source order needs an ordered
   block list in `vacancy_text.structure` and `_vacancy.html`.
3. **P2 jargon on Vacatures.** "Beoordelen gaat vanzelf · 133 in wachtrij" means little to
   a job seeker. Say what it means for them, or show it to admins only.
4. **P3 navigation.** The "Jij" group holds only Account. The back link on the studio
   page is a bare underlined link, while the vacancy page uses an arrow link.
5. **P3 answer weight.** On every yes/no card, Ja is the filled primary button and Nee
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
| Database design | — | — | Not reviewed this round |
| Performance | 6 | 7 | Web memory trimmed (#125); root cause open |
| Reliability | 6 | 7 | OOM kill addressed in part; Test button fixed |
| Security | — | — | Not reviewed this round |
| Maintainability | 7 | 7 | Small, tested changes |
