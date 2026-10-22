#!/usr/bin/env bash
# docker compose with Nexus's encrypted secrets loaded (README: Secrets). Use it wherever you'd
# type docker compose:
#
#   ./scripts/compose.sh up -d --build
#   ./scripts/compose.sh --profile tunnel up -d
#
# Decrypts secrets/nexus.sops.env with your age key into this command's environment only, where
# Compose reads them ahead of .env. Without that file it is plain docker compose.
set -euo pipefail
cd "$(dirname "$0")/.."

default_key=$HOME/.config/nexus/age.key
file=secrets/nexus.sops.env
if [[ -f "$file" ]]; then
  command -v sops >/dev/null || { echo "sops isn't installed: brew install sops age" >&2; exit 1; }
  export SOPS_AGE_KEY_FILE=${SOPS_AGE_KEY_FILE:-$default_key}
  decrypted=$(sops decrypt "$file")
  while IFS= read -r line; do
    [[ "$line" == [A-Za-z_]*=* ]] && export "$line"
  done <<<"$decrypted"
  unset decrypted
fi
exec docker compose "$@"
