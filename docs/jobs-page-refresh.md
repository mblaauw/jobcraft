# Jobs page: vacancy-first refresh

Scope: local jobs page only. No remote deployment or ranking-model changes.

- Put search, filter state and vacancy results before learning/technical details.
- Use one semantic vacancy list: compact desktop rows and stacked mobile cards.
- Label scores as rankings, not probabilities. Show the score prominently with
  colour bands and the fast-judge confidence as supporting context.
- Keep current queue state separate from historical match summaries.
- Keep positive/negative keywords derived from votes as a compact strip above the
  filters, clearly framed as learned signals rather than declared preferences.
- Preserve filters, sorting and pagination after voting or changing status.
- Sort the full filtered dataset before paging; remove the hidden 500-row ceiling.
- Use explicit travel filtering and removable filter chips.
- Add progressive pending feedback; don't reload the page repeatedly while matching.

Validation: template and isolated route/storage tests; local browser checks at
1440, 820 and 390 px, including empty states, filters, sort, actions and keyboard
access. First vacancy should appear within a 900px-high initial viewport and all
row actions must fit without horizontal scrolling. Commit after verification.

## Local verification

- Chromium at 1440, 820 and 390px: first vacancy at 466, 576 and 709px
  respectively (previously 692, 963 and 1259px); no horizontal overflow, all
  vacancy controls fit the screen.
- Browser checks: travel checkbox, search, status tabs, sorting, empty states,
  action payloads/return context, and matching completion without a reload or
  loss of typed search text/scroll position. Mutations and polling fixtures
  intercepted locally; no personal-data changes or provider generation calls.
- Route/storage coverage includes 520 jobs, mixed ISO/RFC/missing dates, safe
  return URLs, cleared votes, interrupted/error states and duplicate submissions.
- Final checks: 153 tests pass, Ruff and JavaScript syntax checks pass. Starlette
  emits a test-client dependency deprecation warning; no validation failures.
- Follow-up UI check with synthetic jobs and votes: colour-coded 86/72/55/41/18
  scores, 92% confidence, top positive/negative chips, drawer focus restore, and
  no horizontal overflow at 1440/820/390px.
