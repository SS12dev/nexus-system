#!/usr/bin/env bash
# Restore Jarvis from a backup folder made by the backup service (see README: Backups).
#
#   ./scripts/restore.sh backups/2026-10-20T213000Z
#
# Replaces every database in the backup and the MinIO files with the backup's copies. Jarvis is
# stopped while it runs. The secrets must be the ones the backup was made with: the token keys
# decrypt the saved Google tokens, and the Postgres and MinIO passwords must match. Each backup
# holds a copy of the encrypted secrets file; put it back in secrets/ first (README: Secrets).
set -euo pipefail
cd "$(dirname "$0")/.."

src=${1:?usage: scripts/restore.sh <backup folder>}
src=${src%/}
name=$(basename "$src")
backup_dir=${BACKUP_DIR:-$(grep -E '^BACKUP_DIR=' .env 2>/dev/null | cut -d= -f2- || true)}
backup_dir=${backup_dir:-./backups}
if [[ ! -f "$src/SHA256SUMS" ]]; then
  echo "No SHA256SUMS in $src; is that a backup folder?" >&2
  exit 1
fi
if [[ "$(cd "$(dirname "$src")" && pwd)" != "$(cd "$backup_dir" && pwd)" ]]; then
  echo "The backup must be inside BACKUP_DIR ($backup_dir), which the backup service mounts." >&2
  exit 1
fi

dc() { ./scripts/compose.sh "$@"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
run() { dc run --rm --no-deps -T "$@"; }

step "Checking $name"
run backup sh -c "cd /backups/$name && sha256sum -c SHA256SUMS"

read -r -p "This replaces all of Jarvis's data with the backup from $name. Type restore to go on: " ok
[[ "$ok" == "restore" ]] || { echo "Stopped."; exit 1; }

step "Stopping Jarvis"
tunnel=$(dc --profile tunnel ps -q cloudflared 2>/dev/null || true)
dc --profile tunnel stop web api orchestrator doc-worker backup litellm langfuse-web langfuse-worker \
  cloudflared minio 2>/dev/null || true
dc up -d postgres

step "Restoring databases"
for dump in "$src"/*.dump; do
  db=$(basename "$dump" .dump)
  echo "  $db"
  run backup sh -c "
    set -e
    until pg_isready -q; do sleep 1; done
    dropdb --if-exists --force '$db'
    createdb '$db'
    pg_restore --no-owner --exit-on-error -d '$db' '/backups/$name/$db.dump'
  "
done

if [[ -f "$src/minio.tar.gz" ]]; then
  step "Restoring files (MinIO)"
  run -v "${COMPOSE_PROJECT_NAME:-jarvis}_minio:/restore" backup \
    sh -c "find /restore -mindepth 1 -delete && tar -xzf '/backups/$name/minio.tar.gz' -C /restore"
fi

step "Starting Jarvis"
if [[ -n "$tunnel" ]]; then dc --profile tunnel up -d; else dc up -d; fi
echo "Restored $name."
