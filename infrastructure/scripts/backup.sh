#!/bin/sh
# Encrypted production Postgres backup (M11). See docs/backup-restore.md.
#
# Settings (root-only file, never in git): /etc/commerceradar-backup.env
#   BACKUP_KEY_FILE=/root/.commerceradar-backup.key   # passphrase file (keep a copy OFF this server)
#   BACKUP_REMOTE=user@host:/path/commerceradar        # rsync/ssh target for the off-server copy
#   BACKUP_DIR=/root/backups/commerceradar            # local encrypted copies
#   KEEP_DAYS=14                                      # local retention
#
# Every run appends one OK / INCOMPLETE / FAILED line to the log. Exit codes:
#   0 = encrypted, verified, copied off-server;  3 = encrypted + verified but NOT copied off-server;
#   1 = failed (nothing new kept).
set -eu
cd "$(dirname "$0")/../.."
[ -f /etc/commerceradar-backup.env ] && . /etc/commerceradar-backup.env
. ./.env 2>/dev/null || true
BACKUP_DIR="${BACKUP_DIR:-/root/backups/commerceradar}"
KEEP_DAYS="${KEEP_DAYS:-14}"
KEY="${BACKUP_KEY_FILE:-/root/.commerceradar-backup.key}"
DB="${POSTGRES_DB:-commerceradar}"
USER="${POSTGRES_USER:-commerceradar}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASE="$BACKUP_DIR/$DB-$STAMP"
TMP="$BASE.dump.partial"
log() { echo "$(date -u +%FT%TZ) $*"; }
fail() { rm -f "$TMP"; log "FAILED: $*"; exit 1; }
trap 'rm -f "$TMP"' EXIT

[ -r "$KEY" ] || fail "encryption key file $KEY missing or unreadable"
[ "$(stat -c %a "$KEY")" = "600" ] || fail "key file $KEY must be mode 600"
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
[ -e "$BASE.dump.gpg" ] && fail "$BASE.dump.gpg already exists (never overwritten)"

docker compose exec -T postgres pg_dump -U "$USER" -d "$DB" -Fc -Z 6 > "$TMP" || fail "pg_dump"
docker compose exec -T postgres pg_restore --list < "$TMP" | grep -q "TABLE DATA public alembic_version" || fail "dump integrity (pg_restore --list)"
gpg --batch --no-tty --symmetric --cipher-algo AES256 --passphrase-file "$KEY" --output "$BASE.dump.gpg" "$TMP" || fail "encryption"
rm -f "$TMP"  # no plaintext dump is kept
gpg --batch --no-tty --quiet --decrypt --passphrase-file "$KEY" "$BASE.dump.gpg" \
  | docker compose exec -T postgres pg_restore --list | grep -q "TABLE DATA public alembic_version" || fail "encrypted copy does not decrypt/restore-list"
(cd "$BACKUP_DIR" && sha256sum "$(basename "$BASE.dump.gpg")" > "$(basename "$BASE.dump.gpg").sha256")
chmod 600 "$BASE.dump.gpg" "$BASE.dump.gpg.sha256"
find "$BACKUP_DIR" -name "$DB-*.dump.gpg*" -mtime +"$KEEP_DAYS" -delete
SIZE="$(du -h "$BASE.dump.gpg" | cut -f1)"

if [ -z "${BACKUP_REMOTE:-}" ]; then
  log "INCOMPLETE: $BASE.dump.gpg ($SIZE) encrypted+verified, but BACKUP_REMOTE not set - only copy is on this server"
  exit 3
fi
rsync -a --ignore-existing -e "ssh -o BatchMode=yes -o ConnectTimeout=30" "$BASE.dump.gpg" "$BASE.dump.gpg.sha256" "$BACKUP_REMOTE/" \
  || { log "INCOMPLETE: off-server copy to $BACKUP_REMOTE failed; local encrypted copy kept: $BASE.dump.gpg"; exit 3; }
log "OK: $BASE.dump.gpg ($SIZE) encrypted, verified, copied to $BACKUP_REMOTE"
