# Backups and restoration

Scripts: `infrastructure/scripts/backup.sh`, `infrastructure/scripts/restore-test.sh`.

## What a backup run does
1. `pg_dump -Fc` of the production database into a temporary file.
2. Integrity check: `pg_restore --list` must show the `alembic_version` table data.
3. Encrypts with GnuPG (AES-256, symmetric, passphrase file) → `commerceradar-<UTC stamp>.dump.gpg`; the plaintext temp file is deleted.
4. Decrypts the encrypted copy and runs `pg_restore --list` again (proves the encrypted file is usable).
5. Writes `<file>.sha256`. Files are timestamped and **never overwritten** (the run fails if the name exists).
6. Deletes local encrypted copies older than `KEEP_DAYS` (default 14).
7. Copies the encrypted file + checksum off-server with `rsync --ignore-existing` (never overwrites remote files).
8. Logs one line: `OK`, `INCOMPLETE` (exit 3: encrypted locally but not copied off-server) or `FAILED` (exit 1).

## Configuration (root-only, not in git): `/etc/commerceradar-backup.env`
```sh
BACKUP_KEY_FILE=/root/.commerceradar-backup.key   # mode 600; ALSO store this passphrase off the server (password manager)
BACKUP_REMOTE=backup@<other-host>:/srv/backups/commerceradar   # ssh key auth from this VPS
BACKUP_DIR=/root/backups/commerceradar
KEEP_DAYS=14
```
Without the passphrase the encrypted backups cannot be restored. Without `BACKUP_REMOTE` the run
ends `INCOMPLETE` — the only copy would be on the production VPS.

## Retention
- Local (VPS): 14 days of encrypted daily dumps.
- Off-server: `rsync --ignore-existing` never deletes; prune on the destination (suggested: 30 dailies + 12 monthlies).

## Schedule (enable only after the destination and key are verified)
```sh
15 3 * * * /opt/commerceradar/infrastructure/scripts/backup.sh >> /var/log/commerceradar-backup.log 2>&1
```
Check: `tail /var/log/commerceradar-backup.log` — every day should end in `OK`.

## Restore
**Test restore (safe, separate throwaway container, never touches production):**
```sh
cd /opt/commerceradar
./infrastructure/scripts/restore-test.sh /root/backups/commerceradar/<file>.dump.gpg
```
It verifies the checksum, decrypts, restores into a new `postgres:17-alpine` container and prints
row counts and the migration revision.

**Real restore (disaster recovery):**
1. `docker compose stop api worker beat web`
2. Verify + decrypt: `sha256sum -c <file>.sha256 && gpg --decrypt --passphrase-file /root/.commerceradar-backup.key <file>.dump.gpg > /root/restore.dump`
3. Recreate the database: `docker compose exec -T postgres dropdb -U commerceradar commerceradar && docker compose exec -T postgres createdb -U commerceradar commerceradar`
4. `docker compose exec -T postgres pg_restore -U commerceradar -d commerceradar --no-owner < /root/restore.dump`
5. Check out the code at the backup's migration revision (`alembic_version`), then `docker compose up -d`.
6. `rm /root/restore.dump`

## Rollback note (measured 2026-11-04 on a restored copy)
Downgrading 0014 → 0009 keeps core rows but drops `store_domains` (identity history),
`suppression_events`, `benchmark_labels` (and later tables) and resurrects merged duplicate
stores. Recovery from a bad release is therefore **restore-from-backup or roll forward**, not
`alembic downgrade`.
