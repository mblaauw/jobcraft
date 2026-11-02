#!/bin/sh
# Start as root only long enough to hand the data folder to the app user, then drop
# privileges for good. Checked per file so a root-owned leftover cannot block the app.
set -eu
if [ "$(id -u)" = "0" ]; then
  find "${APP_DATA_DIR:-/data}" ! -user app -exec chown app:app {} +
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
