#!/bin/sh
# Restore a backup into a SEPARATE, throwaway Postgres container and check it (M11).
# Never touches the production database. Usage: restore-test.sh <dump file> [--downgrade-to REV]
# With --downgrade-to, also runs `alembic downgrade REV` then `upgrade head` on the COPY, and
# prints row counts before/after so data-loss implications of a rollback are measured, not guessed.
set -eu
cd "$(dirname "$0")/../.."
DUMP="$1"
DOWN="${3:-}"
[ -f /etc/commerceradar-backup.env ] && . /etc/commerceradar-backup.env
. ./.env 2>/dev/null || true
case "$DUMP" in
  *.gpg)  # encrypted backup: verify checksum, decrypt to a private temp file
    [ -f "$DUMP.sha256" ] && (cd "$(dirname "$DUMP")" && sha256sum -c "$(basename "$DUMP").sha256")
    PLAIN="$(mktemp /root/restore-test.XXXXXX)"
    gpg --batch --no-tty --quiet --decrypt --passphrase-file "${BACKUP_KEY_FILE:-/root/.commerceradar-backup.key}" "$DUMP" > "$PLAIN"
    DUMP="$PLAIN"
    ;;
esac
USER="${POSTGRES_USER:-commerceradar}"
PASS="${POSTGRES_PASSWORD:-commerceradar}"
NET="$(docker network ls --format '{{.Name}}' | grep -m1 commerceradar)"
NAME=cr-restore-test
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --network "$NET" -e POSTGRES_USER="$USER" -e POSTGRES_PASSWORD="$PASS" -e POSTGRES_DB=restored postgres:17-alpine >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; [ -n "${PLAIN:-}" ] && rm -f "$PLAIN"' EXIT
# The image starts a temporary server for initdb, stops it, then starts the real one:
# wait for the SECOND "ready to accept connections".
for i in $(seq 1 60); do
  [ "$(docker logs "$NAME" 2>&1 | grep -c 'ready to accept connections')" -ge 2 ] && break
  sleep 2
done
docker exec -i "$NAME" pg_restore -U "$USER" -d restored --no-owner < "$DUMP"
COUNTS="select 'alembic', version_num::text from alembic_version union all select 'domains', count(*)::text from domains union all select 'stores', count(*)::text from stores union all select 'products', count(*)::text from products union all select 'store_observations', count(*)::text from store_observations union all select 'discovered_urls', count(*)::text from discovered_urls"
echo "== restored copy"
docker exec "$NAME" psql -U "$USER" -d restored -At -F ' ' -c "$COUNTS"
if [ -n "$DOWN" ]; then
  URL="postgresql+psycopg://$USER:$PASS@$NAME:5432/restored"
  echo "== downgrade copy to $DOWN"
  docker compose run --rm --no-deps -e DATABASE_URL="$URL" api alembic downgrade "$DOWN" 2>&1 | tail -3
  docker exec "$NAME" psql -U "$USER" -d restored -At -F ' ' -c "$COUNTS"
  docker exec "$NAME" psql -U "$USER" -d restored -At -c "select to_regclass('store_domains') is null as store_domains_dropped, to_regclass('suppression_events') is null as suppression_events_dropped, to_regclass('benchmark_labels') is null as benchmark_labels_dropped"
  echo "== upgrade copy back to head"
  docker compose run --rm --no-deps -e DATABASE_URL="$URL" api alembic upgrade head 2>&1 | tail -2
  docker exec "$NAME" psql -U "$USER" -d restored -At -F ' ' -c "$COUNTS"
fi
echo "restore test ok"
