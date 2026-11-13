#!/usr/bin/env bash
# DataWatch — nightly SQLite snapshot (04:30 UTC, user crontab installed
# by deploy/deploy-remote.sh). Snapshots the database out of the running
# container into /srv/datawatch/backups/, verifies the snapshot with
# PRAGMA integrity_check, prunes snapshots older than 7 days. Fails
# loudly (nonzero exit, never a silent fake backup).
#
# Restore: stop the app container, copy a snapshot into a scratch copy
# of the datawatch-data volume, start a scratch container from the same
# image against that volume, and confirm /healthz and a login work.
# Only then is the backup trusted.

set -euo pipefail

BACKUP_DIR="/srv/datawatch/backups"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
SNAPSHOT="$BACKUP_DIR/datawatch-$STAMP.db"

log() { echo "[datawatch-backup $(date -u +%FT%TZ)] $*"; }

mkdir -p "$BACKUP_DIR"

docker ps --format '{{.Names}}' | grep -qx 'datawatch' || {
  log "ERROR: datawatch container is not running — nothing to back up"
  exit 1
}

# Snapshot via the sqlite3 backup API inside the running container
# (handles a concurrently-writing scheduler thread), then copy it out.
docker exec datawatch python3 -c \
  "import sqlite3; src = sqlite3.connect('/data/datawatch.db'); dst = sqlite3.connect('/tmp/dw-backup.db'); src.backup(dst); dst.close(); src.close()"
docker cp datawatch:/tmp/dw-backup.db "$SNAPSHOT"
docker exec datawatch rm -f /tmp/dw-backup.db

python3 -c \
  "import sqlite3; c = sqlite3.connect('file:$SNAPSHOT?mode=ro', uri=True); r = c.execute('PRAGMA integrity_check').fetchone(); assert r[0] == 'ok', r; print('integrity ok')"

find "$BACKUP_DIR" -name 'datawatch-*.db' -mtime +7 -delete
log "backup ok: $SNAPSHOT"
