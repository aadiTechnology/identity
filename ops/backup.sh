#!/bin/sh
# Backup the Identity database to a compressed dump and keep the newest N.
#   DB=identity PGUSER=identity PG_EXEC="docker exec -i <postgres-container>" ./ops/backup.sh [keep]
# PG_EXEC runs pg_dump inside the PostgreSQL container (Coolify: use the container name shown for the database). Leave it empty if pg_dump is on this host.
set -eu
DB="${DB:-identity}"; PGUSER="${PGUSER:-identity}"; KEEP="${1:-14}"; DIR="${BACKUP_DIR:-backups}"
mkdir -p "$DIR"
FILE="$DIR/identity-$(date +%Y%m%d-%H%M%S).dump"
${PG_EXEC:-} pg_dump -U "$PGUSER" --format=custom "$DB" > "$FILE"
[ -s "$FILE" ] || { echo "backup is empty" >&2; rm -f "$FILE"; exit 1; }
echo "wrote $FILE ($(wc -c < "$FILE") bytes)"
ls -1t "$DIR"/identity-*.dump | tail -n +$((KEEP + 1)) | while read -r old; do rm -f "$old"; echo "removed $old"; done
