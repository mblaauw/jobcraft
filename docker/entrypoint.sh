#!/bin/sh
# Start as root only long enough to hand the data folder to the app user, then drop
# privileges for good. Checked per file so a root-owned leftover cannot block the app.
set -eu
if [ "$(id -u)" = "0" ]; then
  data="${APP_DATA_DIR:-/data}"
  mkdir -p "$data"
  find "$data" ! -user app -exec chown -h app:app {} +
  # A `docker compose exec` as root can leave this behind, and it survives a restart.
  rm -rf "${UV_CACHE_DIR:-/tmp/uv-cache}"
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
