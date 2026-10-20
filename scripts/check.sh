#!/usr/bin/env bash
# Run every check CI runs, locally: lint, format, backend tests, web typecheck/tests/build,
# and the Compose config and image builds. Needs uv, npm and Docker.
#
#   ./scripts/check.sh            all checks
#   ./scripts/check.sh --no-images  skip the slow Docker image builds
#
# Tests use throwaway Postgres and Valkey containers on ports 55432/56379, removed at the end.
set -euo pipefail
cd "$(dirname "$0")/.."

build_images=true
[[ "${1:-}" == "--no-images" ]] && build_images=false

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
PG=jarvis-check-postgres
RD=jarvis-check-redis
cleanup() { docker rm -f "$PG" "$RD" >/dev/null 2>&1 || true; }
trap cleanup EXIT

step "Starting test Postgres and Valkey"
cleanup
docker run -d --rm --name "$PG" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=jarvis_test \
  -p 127.0.0.1:55432:5432 pgvector/pgvector:pg17 >/dev/null
docker run -d --rm --name "$RD" -p 127.0.0.1:56379:6379 valkey/valkey:8-alpine >/dev/null
until docker exec "$PG" pg_isready -U postgres -d jarvis_test >/dev/null 2>&1; do sleep 1; done

step "Python: lint and format"
uv sync --frozen
uv run ruff check .
uv run ruff format --check .

step "Python: tests"
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/jarvis_test \
TEST_REDIS_URL=redis://127.0.0.1:56379/15 \
  uv run pytest -q

step "Web: typecheck, tests, build"
(cd apps/web && npm ci --no-audit --no-fund && npm run typecheck && npm test && npm run build)

step "Compose: config"
if [[ -f .env ]]; then docker compose config --quiet
else docker compose --env-file .env.example config --quiet; fi

if $build_images; then
  step "Compose: build images"
  if [[ -f .env ]]; then docker compose build web api orchestrator doc-worker sandbox
  else docker compose --env-file .env.example build web api orchestrator doc-worker sandbox; fi
fi

printf '\n\033[1;32mAll checks passed.\033[0m\n'
