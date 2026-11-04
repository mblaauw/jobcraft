# Backup and restore on the Docker host

`deploy/backup.py` copies the live database with SQLite's online backup (safe while
the app and worker write), checks the copy with `PRAGMA integrity_check`, and packs it
with the account files: `users/` (CVs, generated documents, profiles), `profile/`,
`vacancy-readings/` and `sources.yaml`. Caches, the meaning index and `.env.local` are
not included; the app rebuilds the first two and the secrets stay on the host.

Backups land next to the app folder, in `~/apps/jobcraft-backups` (mode 0700, each
archive 0600):

- `jobcraft-daily-<stamp>.tgz`: once a day from cron, the newest 14 are kept;
- `jobcraft-predeploy-<stamp>.tgz`: before every `scripts/deploy_vps.sh` restart, the
  newest 5 are kept. A failed backup stops the deploy.

The backups sit on the same disk as the app. They protect against mistakes and a bad
deploy, not against losing the server; copy one off the host now and then:

    scp 'user@host:apps/jobcraft-backups/jobcraft-daily-*.tgz' ~/jobcraft-backups/

## Daily schedule

Install once, as the host user that owns `~/apps/jobcraft` (times are the host's, UTC):

    ( crontab -l 2>/dev/null | grep -v 'deploy/backup.py'; \
      echo '30 2 * * * cd ~/apps/jobcraft && /usr/bin/python3 deploy/backup.py --label daily --keep 14 >> ../jobcraft-backups/backup.log 2>&1' ) | crontab -

Every run adds one line to `backup.log`, `backup ok: ...` or `backup mislukt: ...`.

## Check a backup

    cd ~/apps/jobcraft && python3 deploy/backup.py --verify ../jobcraft-backups/<file>.tgz

prints the integrity result, the number of files and the rows in the main tables.

## Restore

Stop the app first, so nothing writes while the files are swapped, and keep what is
there now:

    cd ~/apps/jobcraft
    docker compose -f compose.yaml -f deploy/compose.vps.yml stop app worker
    mv data "data.before-restore-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -m 700 data && tar -xzf ../jobcraft-backups/<file>.tgz -C data
    docker compose -f compose.yaml -f deploy/compose.vps.yml up -d

Always pass both `-f` files: without the overlay the app drops off the proxy network.
The meaning index and caches are rebuilt on start. To restore one account's files
only, extract `users/<id>` from the archive instead of the whole folder.
