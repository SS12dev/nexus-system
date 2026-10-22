#!/bin/sh
# Nexus backups. Each backup is a folder under $BACKUP_OUT named by its UTC time, holding a
# pg_dump of every database, a tarball of the MinIO data, the encrypted secrets file (if there is
# one) and SHA256SUMS. Progress is recorded in
# the jarvis database's backups table, which the Admin page reads.
#
#   nexus-backup loop   check every minute: a "back up now" from the Admin page, or the daily one
#   nexus-backup once   one backup now, then exit
#
# Restore with scripts/restore.sh. Settings (compose.yaml passes them):
#   PGHOST PGUSER PGPASSWORD   Postgres connection
#   BACKUP_DATABASES           databases to dump (default "jarvis litellm langfuse")
#   BACKUP_HOUR_UTC            daily backup at or after this hour (default 21, 02:30 IST)
#   BACKUP_KEEP_DAYS           delete backups older than this (default 14) ...
#   BACKUP_KEEP_MIN            ... but always keep this many (default 3)
#   BACKUP_OUT / MINIO_DATA    where backups go, and the MinIO volume (mounted read-only)
#   SECRETS_FILE               the encrypted secrets file, copied in when present
set -u

OUT=${BACKUP_OUT:-/backups}
MINIO_DATA=${MINIO_DATA:-/minio}
SECRETS_FILE=${SECRETS_FILE:-/secrets/nexus.sops.env}
# Its name before the rename to Nexus.
[ -f "$SECRETS_FILE" ] || [ ! -f /secrets/jarvis.sops.env ] || SECRETS_FILE=/secrets/jarvis.sops.env
DATABASES=${BACKUP_DATABASES:-jarvis litellm langfuse}
HOUR=${BACKUP_HOUR_UTC:-21}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
KEEP_MIN=${BACKUP_KEEP_MIN:-3}

log() { echo "$(date -u +%FT%TZ) $*"; }
sql() { psql -X -q -t -A -v ON_ERROR_STOP=1 -d jarvis -c "$1"; }
quote() { printf "%s" "$1" | sed "s/'/''/g"; }

# Runs one backup for the backups row $1. Returns non-zero on failure.
backup() {
  id=$1
  name=$(date -u +%Y-%m-%dT%H%M%SZ)
  dir="$OUT/$name.partial"
  if ! mkdir -p "$dir"; then
    sql "update backups set status='failed', finished_at=now(), error='Can''t write to $OUT' where id='$id'"
    return 1
  fi
  sql "update backups set status='running', started_at=now(), name='$name' where id='$id'"
  log "backup $name started"
  # Each step exits on failure explicitly: set -e is ignored inside an if condition.
  if (
    cd "$dir" || exit 1
    for db in $DATABASES; do
      if [ "$(psql -X -t -A -d postgres -c "select 1 from pg_database where datname='$db'")" = 1 ]; then
        pg_dump -Fc -Z 6 -d "$db" -f "$db.dump" || exit 1
      fi
    done
    if [ -d "$MINIO_DATA" ]; then
      tar -C "$MINIO_DATA" -czf minio.tar.gz . || exit 1
    fi
    # Encrypted with your age key, so it is safe here; restoring on a new machine needs it.
    if [ -f "$SECRETS_FILE" ]; then
      cp "$SECRETS_FILE" nexus.sops.env || exit 1
    fi
    sha256sum $(ls | grep -v -e '^backup.log$' -e '^SHA256SUMS$') > SHA256SUMS || exit 1
  ) > "$dir/backup.log" 2>&1; then
    mv "$dir" "$OUT/$name"
    size=$(( $(du -sk "$OUT/$name" | cut -f1) * 1024 ))
    sql "update backups set status='ok', finished_at=now(), size_bytes=$size where id='$id'"
    log "backup $name ok ($size bytes)"
    prune
  else
    err=$(tail -n 3 "$dir/backup.log" | tr '\n' ' ' | cut -c1-500)
    rm -rf "$dir"
    sql "update backups set status='failed', finished_at=now(), error='$(quote "$err")' where id='$id'"
    log "backup $name failed: $err"
    return 1
  fi
}

# Delete backups older than KEEP_DAYS, always keeping the newest KEEP_MIN.
prune() {
  cutoff=$(date -u -d "@$(( $(date +%s) - KEEP_DAYS * 86400 ))" +%Y-%m-%dT%H%M%SZ)
  ls -1 "$OUT" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z$' | sort -r \
    | awk -v keep="$KEEP_MIN" -v cutoff="$cutoff" 'NR > keep && $0 < cutoff' \
    | while read -r old; do
        log "pruning $old"
        rm -rf "${OUT:?}/$old"
      done
  # Leftovers from a backup that was interrupted.
  find "$OUT" -maxdepth 1 -name '*.partial' -mmin +360 -exec rm -rf {} +
}

new_row() {
  sql "insert into backups (id, kind, status, created_at) values (gen_random_uuid(), '$1', 'requested', now()) returning id"
}

# A scheduled backup is due once a day after HOUR, or whenever there has been none for 26 hours
# (the Mac was asleep at backup time), including on first start.
due_sql="select
  (extract(hour from now() at time zone 'utc') >= $HOUR
   and not exists (select 1 from backups where kind = 'scheduled'
                   and created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'))
  or not exists (select 1 from backups where kind = 'scheduled'
                 and created_at > now() - interval '26 hours')"

case "${1:-loop}" in
  once)
    backup "$(new_row manual)"
    ;;
  loop)
    mkdir -p "$OUT"
    until sql "select 1 from backups limit 1" > /dev/null 2>&1; do
      log "waiting for the database"
      sleep 5
    done
    sql "update backups set status='failed', finished_at=now(), error='Interrupted (the backup service restarted)' where status='running'"
    log "backups to $OUT, daily after ${HOUR}:00 UTC, kept $KEEP_DAYS days (at least $KEEP_MIN)"
    while true; do
      id=$(sql "select id from backups where status='requested' order by created_at limit 1" 2>/dev/null || true)
      if [ -z "$id" ] && [ "$(sql "$due_sql" 2>/dev/null)" = t ]; then
        id=$(new_row scheduled)
      fi
      if [ -n "$id" ]; then
        backup "$id" || true
      fi
      sleep 60
    done
    ;;
  *)
    echo "usage: nexus-backup loop|once" >&2
    exit 2
    ;;
esac
