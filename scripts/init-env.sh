#!/usr/bin/env sh
# Create .env from .env.example with fresh random secrets. Refuses to overwrite an existing .env.
set -eu
cd "$(dirname "$0")/.."
[ -e .env ] && { echo ".env already exists; leaving it alone." >&2; exit 1; }

rand() { openssl rand -hex "${1:-24}"; }

sed \
  -e "s|^SECRET_KEY=.*|SECRET_KEY=$(rand)|" \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand 16)|" \
  -e "s|^MINIO_ROOT_PASSWORD=.*|MINIO_ROOT_PASSWORD=$(rand 16)|" \
  -e "s|^LITELLM_MASTER_KEY=.*|LITELLM_MASTER_KEY=sk-$(rand)|" \
  -e "s|^CLICKHOUSE_PASSWORD=.*|CLICKHOUSE_PASSWORD=$(rand 16)|" \
  -e "s|^LANGFUSE_SALT=.*|LANGFUSE_SALT=$(rand)|" \
  -e "s|^LANGFUSE_ENCRYPTION_KEY=.*|LANGFUSE_ENCRYPTION_KEY=$(rand 32)|" \
  -e "s|^LANGFUSE_NEXTAUTH_SECRET=.*|LANGFUSE_NEXTAUTH_SECRET=$(rand)|" \
  -e "s|^LANGFUSE_SECRET_KEY=.*|LANGFUSE_SECRET_KEY=sk-lf-$(rand)|" \
  -e "s|^LANGFUSE_ADMIN_PASSWORD=.*|LANGFUSE_ADMIN_PASSWORD=$(rand 12)|" \
  -e "s|^DEV_LOGIN_TOKEN=.*|DEV_LOGIN_TOKEN=$(rand 12)|" \
  -e "s|^SANDBOX_TOKEN=.*|SANDBOX_TOKEN=$(rand)|" \
  -e "s|^WORKBENCH_TOKEN=.*|WORKBENCH_TOKEN=$(rand)|" \
  -e "s|^TOKEN_ENCRYPTION_KEYS=.*|TOKEN_ENCRYPTION_KEYS=$(rand 32)|" \
  .env.example > .env
chmod 600 .env
echo "Wrote .env. Add OPENAI_API_KEY (and Google OIDC credentials), then: docker compose up -d --build"
echo "For production, encrypt the secrets: ./scripts/secrets.sh init (README: Secrets)"
