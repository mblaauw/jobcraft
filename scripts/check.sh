#!/usr/bin/env bash
# The one check entry point: the gates CI runs, in CI's order, on this checkout.
# Prints the revision first so an evidence block can quote exactly what passed.
# Extra arguments go to pytest (for example `scripts/check.sh -k matching`).
# CI sets UV_RUN_FLAGS=--no-sync after `uv sync --locked`; locally uv syncs itself.
set -euo pipefail
cd "$(dirname "$0")/.."

head=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")
if git diff --quiet && git diff --cached --quiet 2>/dev/null; then state="clean"; else state="dirty"; fi
echo "check: revision ${head} (${state} working tree)"

run="uv run ${UV_RUN_FLAGS:-}"
$run ruff check .
node --check app/static/job-drawer.js
$run pytest -q "$@"
echo "check: passed on ${head} (${state})"
