#!/usr/bin/env bash
# Zaxira (root): PostgreSQL bazasi + yuklangan cheklar. Eski nusxalar KEEP_DAYS kundan keyin o'chiriladi.
# Ishlatish:  bash /opt/pbxbot/saas/deploy/backup.sh      (kunlik cron: deploy/pbxbot-backup.cron)
set -euo pipefail

DEST="${BACKUP_DIR:-/var/backups/pbxbot}"
KEEP_DAYS="${KEEP_DAYS:-14}"
APP=/opt/pbxbot

umask 077
mkdir -p "$DEST"
STAMP=$(date +%F_%H%M)

DB_FILE="$DEST/db_$STAMP.sql.gz"
sudo -u postgres pg_dump pbxbot | gzip > "$DB_FILE"
[ -s "$DB_FILE" ] || { echo "XATO: baza zaxirasi bo'sh" >&2; rm -f "$DB_FILE"; exit 1; }

if [ -d "$APP/saas/uploads" ]; then
  tar -czf "$DEST/uploads_$STAMP.tar.gz" -C "$APP/saas" uploads
fi

find "$DEST" -type f -mtime +"$KEEP_DAYS" -delete
echo "OK: $DB_FILE ($(du -h "$DB_FILE" | cut -f1))"
