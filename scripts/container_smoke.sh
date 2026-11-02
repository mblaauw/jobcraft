#!/usr/bin/env bash
# Build and run the real web and worker containers on throwaway data, with AI off.
# Needs Docker. Refuses to run where a real .env.local or data folder exists.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -e .env.local ] || [ -e data ] || [ -e seed ]; then
  echo "error: .env.local, data/ of seed/ bestaat al; draai dit in een schone checkout" >&2
  exit 2
fi
cleanup() {
  docker compose logs --tail=40 || true
  docker compose down --volumes || true
  # Files in data/ now belong to the container's app user.
  docker run --rm -v "$PWD:/work" --entrypoint rm jobcraft:local -rf /work/data /work/seed || true
  rm -f .env.local
}
trap cleanup EXIT
mkdir -p data seed
cat > .env.local <<ENV
APP_USER=owner
APP_PASSWORD=container-smoke-only
SYSTEMONE_ENABLED=0
SCAN_TIMES=
MATCH_AFTER_SCAN=0
ENV
docker compose build
docker compose up -d
run() { docker compose exec -T --user app worker uv run --no-dev python -c "$1"; }
for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:18080/healthz >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:18080/healthz
curl -fsS -o /dev/null http://127.0.0.1:18080/login
for service in app worker; do
  uid=$(docker compose exec -T "$service" sh -c 'cat /proc/1/status' | awk '/^Uid:/ {print $2}')
  [ "$uid" = "1000" ] || { echo "error: $service draait als uid $uid" >&2; exit 1; }
done
for _ in $(seq 1 60); do
  beat=$(run 'from app import store; print(store.meta_get("worker_heartbeat") or "")' | tail -1)
  [ -n "$beat" ] && break
  sleep 2
done
[ -n "$beat" ] || { echo "error: geen heartbeat van de worker" >&2; exit 1; }
echo "worker heartbeat: $beat"
run '
import pathlib, tempfile
from docx import Document
from app.document_conversion import convert_pdf
path = pathlib.Path(tempfile.mkdtemp()) / "smoke.docx"
document = Document()
document.add_paragraph("Jobcraft")
document.save(path)
result = convert_pdf(path)
assert result and pathlib.Path(result).stat().st_size > 0
print("pdf ok")
'
echo "container smoke passed"
