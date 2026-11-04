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
  docker logs --tail=20 jobcraft-smoke-proxy 2>/dev/null || true
  docker rm -f jobcraft-smoke-proxy >/dev/null 2>&1 || true
  docker compose down --volumes || true
  docker network rm jobcraft-smoke-proxy >/dev/null 2>&1 || true
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

# The example proxy config, in front of the running app, under /jobcraft/.
network=jobcraft-smoke-proxy
docker network create "$network" >/dev/null
docker network connect --alias jobcraft "$network" "$(docker compose ps -q app)"
proxy_dir=$(mktemp -d)
{ echo 'server { listen 80;'; cat deploy/nginx.conf.example; echo '}'; } > "$proxy_dir/default.conf"
docker run -d --name jobcraft-smoke-proxy --network "$network" -p 127.0.0.1:18081:80 \
  -v "$proxy_dir/default.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.27-alpine >/dev/null
base=http://127.0.0.1:18081/jobcraft
for _ in $(seq 1 30); do
  curl -fsS "$base/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$base/healthz"
jar="$proxy_dir/cookies"
page=$(curl -fsS -c "$jar" "$base/login")
case "$page" in
  *'href="/jobcraft/static/app.css"'*) ;;
  *) echo "error: stylesheet zonder /jobcraft" >&2; exit 1 ;;
esac
if printf '%s' "$page" | grep -E '(href|src|action)="/[^j]' >/dev/null; then
  echo "error: pagina bevat nog een link zonder /jobcraft" >&2
  exit 1
fi
curl -fsS -o /dev/null "$base/static/fonts/schibsted-grotesk-latin-wght-normal.woff2"
# Read to the end: grep -q closing the pipe early would fail curl under pipefail.
contains() {
  body=$(curl -fsS "$base/$1")
  case "$body" in
    *"$2"*) ;;
    *) echo "error: $1 mist $2" >&2; exit 1 ;;
  esac
}
contains static/app.css 'url("fonts/'
lacks() {
  body=$(curl -fsS "$base/$1")
  case "$body" in
    *"$2"*) echo "error: $1 bevat nog $2" >&2; exit 1 ;;
  esac
}
# Scripts take the prefix from the page they run in, so no root address may remain.
contains static/start.js 'appRoot + "start/'
lacks static/start.js 'fetch("/'
contains static/job-drawer.js 'replace(/#i-'  # swaps only the icon, keeping the page's own address
csrf=$(echo "$page" | grep -oE 'name="_csrf" value="[^"]+"' | sed -E 's/.*value="([^"]+)"/\1/')
target=$(curl -sS -o /dev/null -b "$jar" -c "$jar" -w '%{redirect_url}' \
  --data-urlencode "_csrf=$csrf" --data-urlencode username=owner \
  --data-urlencode password=container-smoke-only "$base/login")
case "$target" in
  */jobcraft/*) ;;
  *) echo "error: inloggen stuurt door naar $target" >&2; exit 1 ;;
esac
curl -fsS -o /dev/null -b "$jar" "$base/jobs"
echo "proxy path passed"
echo "container smoke passed"
