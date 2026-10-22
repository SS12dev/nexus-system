#!/usr/bin/env bash
# One-time move of an install that still runs as the Compose project "jarvis" to "nexus":
#
#   ./scripts/move-to-nexus.sh
#
# 1. backs up the running install, then stops it (its volumes stay);
# 2. copies every jarvis_* volume to nexus_*;
# 3. renames the Postgres database and user, the MinIO bucket and the LiteLLM key models to
#    nexus, and gives Langfuse's old traces their own org name;
# 4. moves the age key to ~/.config/nexus and the jarvis names in .env;
# 5. starts Nexus as "nexus", with the tunnel if it was running.
# The jarvis_* volumes are left for you to delete once Nexus looks right:
#   docker volume rm jarvis_postgres jarvis_redis jarvis_minio jarvis_clickhouse \
#     jarvis_clickhouse-logs jarvis_workspaces
set -euo pipefail
cd "$(dirname "$0")/.."

VOLUMES=(postgres redis minio clickhouse clickhouse-logs workspaces)
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
dc() { ./scripts/compose.sh "$@"; }

if docker volume inspect nexus_postgres >/dev/null 2>&1; then
  echo "nexus_postgres already exists, so the move has been done." >&2
  exit 1
fi
docker volume inspect jarvis_postgres >/dev/null 2>&1 || { echo "No jarvis_postgres volume." >&2; exit 1; }

step "Age key"
if [[ ! -f ~/.config/nexus/age.key && -f ~/.config/jarvis/age.key ]]; then
  mkdir -p ~/.config/nexus && chmod 700 ~/.config/nexus
  mv ~/.config/jarvis/age.key ~/.config/nexus/age.key
  rmdir ~/.config/jarvis 2>/dev/null || true
  echo "  moved to ~/.config/nexus/age.key"
fi

step "Settings in .env"
if [[ -f .env ]]; then
  perl -pi -e 's/\bjarvis-(nano|mini|pro|max|embed)\b/nexus-$1/g; s/pk-lf-jarvis/pk-lf-nexus/g;
    s/admin\@jarvis\.local/admin\@nexus.local/g; s/jarvis\.servhub\.cc/nexus.servhub.cc/g;
    s/secrets\/jarvis\.sops\.env/secrets\/nexus.sops.env/g;
    $_ = "" if /^\s*#?\s*(OLD_HOST|JARVIS_[A-Z_]+)=/' .env
  grep -qi jarvis .env && echo "  .env still mentions jarvis somewhere; check it by hand" || true
fi

step "Backing up the running install"
tunnel=$(docker ps -q -f name=^jarvis-cloudflared-1$)
docker exec jarvis-backup-1 nexus-backup once

step "Stopping the jarvis project (volumes stay)"
dc -p jarvis --profile tunnel down --remove-orphans

step "Copying volumes"
for v in "${VOLUMES[@]}"; do
  echo "  jarvis_$v -> nexus_$v"
  docker volume create --label com.docker.compose.project=nexus \
    --label com.docker.compose.volume="$v" "nexus_$v" >/dev/null
  docker run --rm --entrypoint sh -v "jarvis_$v:/from:ro" -v "nexus_$v:/to" \
    pgvector/pgvector:pg17 -c 'cp -a /from/. /to/'
done

step "Postgres: database and user"
dc up -d --wait postgres
pg() { docker exec -i nexus-postgres-1 sh -c "psql -X -q -v ON_ERROR_STOP=1 $*"; }
# jarvis made the cluster, so it owns the system catalogs and can't be dropped; rename it
# instead, from a temporary superuser (a role can't rename itself). The password comes from the
# container's own environment and never reaches this shell.
pg '-U jarvis -d postgres' <<'SQL'
CREATE ROLE rename_tmp SUPERUSER LOGIN;
SQL
pg '-U rename_tmp -d postgres -v pw="$POSTGRES_PASSWORD"' <<'SQL'
ALTER DATABASE jarvis RENAME TO nexus;
ALTER ROLE jarvis RENAME TO nexus;
ALTER ROLE nexus PASSWORD :'pw';
SQL
pg '-U nexus -d postgres' <<'SQL'
DROP ROLE rename_tmp;
SQL

step "LiteLLM: keys allowed the jarvis-* models"
pg '-U nexus -d litellm' <<'SQL'
DO $$ BEGIN
  UPDATE "LiteLLM_VerificationToken"
     SET models = ARRAY(SELECT replace(m, 'jarvis-', 'nexus-') FROM unnest(models) m),
         key_alias = replace(key_alias, 'jarvis-', 'nexus-')
   WHERE array_to_string(models, ',') LIKE '%jarvis-%' OR key_alias LIKE 'jarvis-%';
  UPDATE "LiteLLM_UserTable"
     SET models = ARRAY(SELECT replace(m, 'jarvis-', 'nexus-') FROM unnest(models) m)
   WHERE array_to_string(models, ',') LIKE '%jarvis-%';
EXCEPTION WHEN undefined_table OR undefined_column THEN
  RAISE NOTICE 'LiteLLM tables differ; nothing changed: %', SQLERRM;
END $$;
SQL

step "Langfuse: old traces keep their org, renamed"
pg '-U nexus -d langfuse' <<'SQL'
DO $$ BEGIN
  UPDATE users SET email = 'admin@nexus.local' WHERE email = 'admin@jarvis.local';
  UPDATE organizations SET name = 'Nexus (before rename)' WHERE id = 'jarvis';
  UPDATE projects SET name = 'Nexus (before rename)' WHERE id = 'jarvis';
EXCEPTION WHEN undefined_table OR undefined_column THEN
  RAISE NOTICE 'Langfuse tables differ; nothing changed: %', SQLERRM;
END $$;
SQL

step "MinIO: bucket jarvis -> nexus"
dc up -d --wait minio
docker exec nexus-minio-1 sh -c '
  set -e
  export MC_CONFIG_DIR=/tmp/mc
  mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
  if mc ls local/jarvis >/dev/null 2>&1; then
    mc mb --ignore-existing local/nexus >/dev/null
    mc mirror --preserve --quiet local/jarvis local/nexus
    mc rb --force local/jarvis >/dev/null
  fi
  mc ls local'

step "Redis: dropping keys under the old prefix"
dc up -d --wait redis
docker exec nexus-redis-1 sh -c "valkey-cli --scan --pattern 'jarvis:*' | xargs -r valkey-cli del >/dev/null"

step "Starting Nexus"
if [[ -n "$tunnel" ]]; then dc --profile tunnel up -d --build; else dc up -d --build; fi

step "Langfuse: the API key moves to the new nexus project"
# Langfuse makes the nexus org and project on start, then fails to add the key because the old
# one has the same secret; hand it the old key instead.
until docker exec nexus-postgres-1 psql -U nexus -d langfuse -Atc \
  "select 1 from projects where id = 'nexus'" 2>/dev/null | grep -q 1; do sleep 2; done
docker exec nexus-postgres-1 psql -U nexus -d langfuse -Atc \
  "update api_keys set public_key = 'pk-lf-nexus', project_id = 'nexus' where public_key = 'pk-lf-jarvis'"
dc restart langfuse-web langfuse-worker litellm
echo
echo "Done. The jarvis_* volumes are still there; delete them once Nexus looks right."
