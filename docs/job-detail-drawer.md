# Job detail drawer (local only)

- Enhance existing detail links with a native modal dialog; keep normal links as
  the no-JavaScript, new-tab and direct-link fallback.
- Fetch the existing server-rendered detail page and reuse its marked content.
  No second detail renderer, iframe, UI framework or new provider calls.
- Push one history entry on open. Back/close/Escape restore the exact underlying
  list, scroll and originating link focus. Forward reopens the detail.
- Desktop: right-hand panel (~680px), independently scrollable with a sticky
  header. Mobile: fullscreen. Respect reduced-motion preferences.
- Intercept detail forms only inside the dialog; retain existing POST routes and
  redirects. Keep row vote/status/score feedback in sync without reordering the
  list. Poll document generation inside the drawer, never refresh the list.
- Obtain all request URLs from rendered links/form actions to preserve future
  compatibility with the path-prefix proxy.
- Verify browser history, keyboard/focus, scroll, errors, races, forms, document
  completion, full-page fallback and mobile sizing with isolated fixtures.

## Verified locally

- 96 pytest cases pass; Ruff and both JavaScript syntax checks pass. One existing
  Starlette test-client dependency deprecation warning remains.
- Chromium: laptop widths 1440/1366px with a 680px drawer; 390px mobile uses the
  full viewport. Existing list layout checks at 1440/820/390px still pass.
- Opened vacancy 80: close/Escape/backdrop and browser Back restore exact scroll
  and focus; Forward reopens it and remembers which of the two links was used.
- Verified keyboard focus wrapping, reduced motion, full-page/no-JavaScript
  fallback, retained filters/anchors, load failure/retry and failed POST recovery.
- Mocked votes/status updates reconcile the original row without reordering;
  a POST completing after close still reconciles that row. Stale detail responses
  cannot replace a newer selection.
- Mocked document generation polls only the panel, strips meta-refresh, and
  preserves focused section headings. Stable IDs also cover document links.
- Document start is guarded across concurrent views in the single-worker app;
  a temporary-database concurrency test verifies only one generation starts.
- All browser mutations/provider generation were intercepted. No remote access,
  deployments or personal-data mutations were performed during verification.
