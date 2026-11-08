#!/bin/sh
# Proves a backup can be restored: loads the newest dump into a scratch database and compares row counts with the source.
#   DB=identity PGUSER=identity PG_EXEC="docker exec -i <postgres-container>" ./ops/restore_drill.sh
# Run it after every backup change and at least once a quarter. It never touches the source database.
set -eu
DB="${DB:-identity}"; PGUSER="${PGUSER:-identity}"; DIR="${BACKUP_DIR:-backups}"; SCRATCH="${DB}_restore_drill"
FILE="$(ls -1t "$DIR"/identity-*.dump | head -n 1)"
[ -n "$FILE" ] || { echo "no backup found in $DIR" >&2; exit 1; }
SQL() { ${PG_EXEC:-} psql -U "$PGUSER" -d "$1" -tA -c "$2"; }
${PG_EXEC:-} psql -U "$PGUSER" -d postgres -q -c "drop database if exists $SCRATCH" -c "create database $SCRATCH"
${PG_EXEC:-} pg_restore -U "$PGUSER" -d "$SCRATCH" --no-owner < "$FILE"
status=0
for t in users tenants tenant_members products subscriptions oauth_clients signing_keys audit_log; do
  a="$(SQL "$DB" "select count(*) from $t")"; b="$(SQL "$SCRATCH" "select count(*) from $t")"
  if [ "$a" = "$b" ]; then echo "ok    $t: $a rows"; else echo "DIFF  $t: source $a, restored $b"; status=1; fi
done
${PG_EXEC:-} psql -U "$PGUSER" -d postgres -q -c "drop database $SCRATCH"
[ $status -eq 0 ] && echo "restore drill passed using $FILE" || echo "restore drill FAILED" >&2
exit $status
