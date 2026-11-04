#!/bin/sh
# Production Postgres backup (M11). Daily via cron:
#   15 3 * * * /opt/commerceradar/infrastructure/scripts/backup.sh >> /var/log/commerceradar-backup.log 2>&1
# Custom-format pg_dump (compressed, restorable with pg_restore), verified after writing,
# 14 days kept. Copy BACKUP_DIR off the server as well — a backup on the same disk is not enough.
set -eu
cd "$(dirname "$0")/../.."
BACKUP_DIR="${BACKUP_DIR:-/root/backups/commerceradar}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
. ./.env 2>/dev/null || true
DB="${POSTGRES_DB:-commerceradar}"
USER="${POSTGRES_USER:-commerceradar}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/$DB-$STAMP.dump"

docker compose exec -T postgres pg_dump -U "$USER" -d "$DB" -Fc -Z 6 > "$OUT.tmp"
# verify: the archive must list its table of contents, including alembic_version
docker compose exec -T postgres pg_restore --list < "$OUT.tmp" | grep -q "TABLE DATA public alembic_version"
mv "$OUT.tmp" "$OUT"
chmod 600 "$OUT"
find "$BACKUP_DIR" -name "$DB-*.dump" -mtime +"$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
