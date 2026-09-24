#!/usr/bin/env bash
# Daily Postgres backup for cal.diy. Install at /opt/cal-diy/scripts/backup-db.sh
set -euo pipefail
DIR=/opt/cal-diy
BACKUP_DIR="$DIR/backups"
mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
docker exec cal-diy-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$BACKUP_DIR/calendso-$STAMP.sql.gz"
# Keep 14 days
find "$BACKUP_DIR" -name 'calendso-*.sql.gz' -mtime +14 -delete
echo "backup written: $BACKUP_DIR/calendso-$STAMP.sql.gz"
