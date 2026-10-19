#!/usr/bin/env bash
# Deploy Jobcraft to a Docker host and restart the app.
#
#   VPS_HOST=user@host ./scripts/deploy_vps.sh
#   SYNC_DATA=1 VPS_HOST=user@host ./scripts/deploy_vps.sh   # first deploy: data + seed
#
# The remote .env.local holds its own credentials and is never uploaded.
# Set up deploy/compose.vps.yml when the host uses an external proxy network.
set -euo pipefail

HOST="${1:-${VPS_HOST:-}}"
if [ -z "$HOST" ]; then
  echo "error: geef een host op: VPS_HOST=user@host $0" >&2
  exit 2
fi
DIR="${VPS_DIR:-apps/jobcraft}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SYNC_DATA="${SYNC_DATA:-0}"
EXCLUDES=(
  --include '.env.example' --exclude '.env*'
  --exclude '.git/' --exclude '.venv/' --exclude '.runtime/'
  --exclude 'data/runtime/' --exclude '__pycache__/' --exclude '.pytest_cache/'
  --exclude '.ruff_cache/' --exclude '.DS_Store' --exclude '*.pyc'
)
if [ "$SYNC_DATA" = "1" ]; then
  echo "== checkpoint sqlite before copying data =="
  if command -v sqlite3 >/dev/null 2>&1 && [ -f data/app.db ]; then
    sqlite3 data/app.db "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null || true
  fi
else
  EXCLUDES+=(--exclude 'data/' --exclude 'seed/')
  echo "== data/ and seed/ are NOT synced (SYNC_DATA=1 to include them) =="
fi

echo "== rsync to $HOST:~/$DIR =="
ssh -o BatchMode=yes "$HOST" "mkdir -p ~/$DIR"
rsync -az --stats "${EXCLUDES[@]}" ./ "$HOST:$DIR/"

echo "== build + restart =="
ssh -o BatchMode=yes "$HOST" "set -euo pipefail; cd ~/$DIR; test -s .env.local || { echo 'error: remote .env.local ontbreekt of is leeg' >&2; exit 1; }; chmod 600 .env.local; BUILD_LOG=\$(mktemp); if ! docker compose -f compose.yaml -f deploy/compose.vps.yml build > \"\$BUILD_LOG\" 2>&1; then tail -40 \"\$BUILD_LOG\"; rm -f \"\$BUILD_LOG\"; exit 1; fi; tail -3 \"\$BUILD_LOG\"; rm -f \"\$BUILD_LOG\"; docker compose -f compose.yaml -f deploy/compose.vps.yml up -d; sleep 5; docker compose -f compose.yaml -f deploy/compose.vps.yml ps; curl -s -o /dev/null -w 'health=%{http_code}\n' http://127.0.0.1:18080/healthz"

echo "== done =="
echo "  tunnel:  ssh -N -L 18080:127.0.0.1:18080 $HOST   (http://127.0.0.1:18080)"
echo "  public:  configure your reverse proxy; see deploy/nginx.conf.example"
