#!/bin/bash
# Restore drill for the pg_dumpall backups the production tasks write (db-backup, 03:00 daily).
# Usage: ops/restore_drill.sh <source_container> <superuser> [dump.sql.gz]
# Dumps the source (or uses the given dump), restores into a scratch container, compares row counts of every table in every database.
set -euo pipefail
SRC="$1"; SU="$2"; DUMP="${3:-}"
SCR=aadi-restore-drill
[ "$(docker inspect -f {{.State.Running}} "$SRC" 2>/dev/null)" = true ] || { echo "source container $SRC is not running" >&2; exit 2; }
docker rm -f $SCR >/dev/null 2>&1 || true
if [ -z "$DUMP" ]; then DUMP="$(mktemp -t drill.XXXXXX).sql.gz"; docker exec "$SRC" pg_dumpall -U "$SU" | gzip > "$DUMP"; fi
[ "$(wc -c < "$DUMP")" -gt 1000 ] || { echo "dump is empty or too small" >&2; exit 1; }
docker run -d --name $SCR -e POSTGRES_PASSWORD=drill postgres:16-alpine >/dev/null
until docker exec $SCR pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
gunzip -c "$DUMP" | docker exec -i $SCR psql -U postgres -q -v ON_ERROR_STOP=0 >/dev/null 2>"${DUMP}.err" || true
counts() { # container user -> "db.table count" lines
  for db in $(docker exec "$1" psql -U "$2" -Atc "select datname from pg_database where datname not in ('postgres','template0','template1') order by 1"); do
    for t in $(docker exec "$1" psql -U "$2" -d "$db" -Atc "select quote_ident(schemaname)||'.'||quote_ident(tablename) from pg_tables where schemaname not in ('pg_catalog','information_schema') order by 1"); do
      echo "$db.$t $(docker exec "$1" psql -U "$2" -d "$db" -Atc "select count(*) from $t")"
    done
  done
}
counts "$SRC" "$SU" > "${DUMP}.src"; counts $SCR postgres > "${DUMP}.dst"
[ "$(wc -l < "${DUMP}.src")" -gt 0 ] || { echo "source has no tables: refusing to pass" >&2; exit 1; }
if diff -q "${DUMP}.src" "${DUMP}.dst" >/dev/null; then echo "RESTORE DRILL PASS: $(wc -l < "${DUMP}.src") tables match, dump $(wc -c < "$DUMP") bytes"; docker rm -f $SCR >/dev/null; else echo "RESTORE DRILL FAIL"; diff "${DUMP}.src" "${DUMP}.dst" | head; exit 1; fi
