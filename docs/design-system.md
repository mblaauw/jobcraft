# Design system: "vacature-stempel", second edition

Status: built on branch `design/product-redesign`, 2026-11-14. This is the reference
for anyone who changes a template or a stylesheet. It describes what exists; when the
code and this page disagree, the code is right and this page needs an edit.

## The idea in one paragraph

The page is paper. Every object on it (a vacancy, an application, a part of a CV) is
a white sheet with a hairline edge. The frame is forest green. The one bold element
is the match stamp: a lime disc whose ring is the score. Everything else stays quiet
so that the stamp, the next thing to do, and the text you came to read stand out.

## Rules of thumb

1. **The object comes first.** A page opens on the thing the person came for, not on
   the controls for it. Filters stand beside a list or fold behind one button.
2. **One way to do each thing.** One tab pattern, one button set, one sheet, one row
   of settings. If a page needs something new, add it here first.
3. **Lime is a signal, not decoration.** It marks a strong match, a new item, and the
   one primary action on a dark band. It is never a background for chrome.
4. **Say it once.** A page explains itself in its lede; a section in one muted line.
5. **Tokens only.** No colour, size or distance is written as a raw value in a page
   rule. If a token is missing, add the token.

## Files

| File | Holds | Loaded by |
|---|---|---|
| `app/static/app.css` | tokens, base, shell, controls, tabs, chips, sheets, feedback, stamp, and every page except the two below | `base.html`, `login.html`, `invite.html` |
| `app/static/cv.css` | Profiel: Mijn cv, Wat ik zoek, Notities | `profile.html` through `{% block styles %}` |
| `app/static/studio.css` | the application studio: plan and desk | `studio.html` through `{% block styles %}` |

There is no build step. Rules are grouped under section banners in the order of the
list at the top of `app.css`; a page rule goes in its page's section.

## Tokens

Defined on `:root` in `app.css`, with dark values under `prefers-color-scheme` and
again under `:root[data-theme="dark"]`. Change both dark blocks together.

- **Paper and sheets:** `--paper` (page), `--surface` (sheet), `--surface-2` (sheet
  inside a sheet, table head, hover), `--sunk` (wells, quiet chips), `--line`,
  `--line-strong`, `--field-line` (the edge of a control: 3:1 against a sheet).
- **Ink:** `--ink`, `--ink-2` (secondary), `--ink-3` (tertiary; still 4.5:1 on every
  surface above).
- **Brand:** `--forest`, `--forest-2`, `--forest-3` (frame), `--band` (dark panels),
  `--moss` (actions and links), `--lime`, `--lime-strong`, `--lime-soft`.
- **Meaning:** `--ok`, `--clay` (attention), `--danger`, each with a `-soft` fill.
- **State:** `--selected` / `--on-selected` (a chosen chip, the count on the current
  tab), `--stamp-arc` / `--stamp-track` (the ring of a strong match). These flip in
  dark mode, so components need no theme-specific rules.
- **Type:** `--fs-2xs` 12, `--fs-xs` 13, `--fs-sm` 14, `--fs-base` 15, `--fs-md` 17,
  `--fs-lg` 20, `--fs-xl` 24, `--fs-2xl` 32. Tabular figures only for bare numbers:
  in Schibsted Grotesk they also widen the full stop and the colon.
- **Distance:** `--sp-1` 4 to `--sp-9` 56, a 4px grid.
- **Shape:** `--r-xs` 6, `--r-sm` 8 (controls), `--r` 12, `--r-lg` 16 (sheets),
  `--r-pill`. `--ctl` 40 and `--ctl-sm` 32 are control heights; both grow on a
  coarse pointer.

## Components

| Component | Class | Notes |
|---|---|---|
| Page head | `.page-head`, `.lede`, `.page-actions` | one `h1`; actions on the right |
| Sheet | `.panel` (or `.card`), `.panel-flush`, `.panel-head` | title inside a sheet is 17px |
| Button | `.button` with `.primary`, `.quiet`, `.danger`, `.small`, `.block` | one primary per view; shows a spinner as `.is-saving` |
| Icon button | `.icon-button`, `aria-pressed` | always with `aria-label` and `title` |
| Field | `.field` around label, control and `.hint` | 16px on phones so iOS does not zoom |
| Choices | `.choice-group` / `.choice`, `.toggle-chip`, `.switch` | real inputs underneath |
| Tabs | `.status-tabs` or `.studio-tabs` with `aria-current="page"`, `.tab-count` | parts of a page |
| Segmented | `.segmented` | two or three views of the same list |
| Chips | `.chip` with `-ok`, `-warn`, `-lime`; `.badge`; `.dot` | facts and states, not actions |
| Stamp | `ui.stamp(job)` renders `.vacancy-score` with `--p` | `.low` `.mid` `.high`, `.is-provisional`, `.big` |
| Feedback | `.banner` with `.ok` / `.warn`, `ui.wait(...)`, `.empty-state` | an empty state offers the next step |
| Settings | `.settings` > `.setting` > `.setting-about` + `.setting-body` | Account and Instellingen |
| Index beside content | `.cv-layout` / `.wishes` with `.cv-nav` | Profiel tabs |
| Focused page | `{% block main_class %} is-focused{% endblock %}` | one question or one task |

## The stamp

`ui.stamp` sets `--p` to the score. The ring is a conic gradient masked to a band
(`--ring` wide); the face is a pseudo-element inside it. Below 50 the ring is grey,
from 50 moss on white, from 70 a lime disc with a forest ring. A dashed ring
(`.is-provisional`) means only the quick assessment exists; a dashed outline without
a ring means no score yet. `job-drawer.js` updates `--p` when a score changes.

## Layout

- Rail of 244px, content up to 1160px. Below 900px the rail becomes a top bar and a
  bottom tab bar: the main places, and "Meer" (a `<details data-menu>` sheet) when
  there is more than Account left.
- Vacatures: filters in a sticky column beside the list from 1181px; below that one
  search row and a Filters button (`.filters-fold`).
- Breakpoints in use: 1180, 1040 (Profiel index), 980 (studio desk), 900, 640, 520.

## Behaviour that scripts rely on

`ui.js`: `data-autosubmit` (also for a control tied to the form with `form=`),
`data-pending-form`, `data-swap`, `data-auto-refresh`, `data-elapsed`, `data-copy`,
`data-menu`, `data-source-filter`, `data-wait-sync` (parts of a waiting block that each
poll swaps in, so the current step moves). `jobs.js`: `.filters-more`, `#reach-minutes` and
its `data-reach-*` partners, `#jobs-progress`, and the keys j, k, Enter, s, x and /.
`job-drawer.js`: `[data-job-detail]`, `[data-job-link]`, `.vacancy-row`,
`.vacancy-votes`, `.vacancy-save`, `.vacancy-flags`, `.status-tabs a[data-status]`.
Keep these names when restructuring a template.

## Checks before a change ships

`scripts/check.sh`, then the page in the browser at 1440, 1024 and 375 wide, in
light and dark. `.claude/skills/career-gui-review` holds the review checklist.

Use a synthetic demo, never real data. Three things need more than the dev server:

- **Document previews.** The studio shows page images only where LibreOffice and
  `pdftoppm` exist. Run the demo in the app image (`docker build -t jobcraft:local .`)
  with its data folder mounted at `/data`; the image has both.
- **Screens while the AI works.** `scripts/dev_ai_stub.py` answers like the AI service
  and System One after a delay you set in its `control.json`, or fails on purpose, so
  the waiting, running and error states can be seen. Its answers are filler.
- **Accessibility.** An axe-core scan (WCAG 2.1 AA plus best practice) through
  Playwright with `bypassCSP`, since the page's policy blocks injecting it in a normal
  browser. Axe leaves text on the rail gradient and the stamp disc undecided; check
  those against the colour actually behind them. On 2026-11-15 all pages, as a job
  seeker and as an administrator, at 1440 and 375 in both themes, had no violations.
