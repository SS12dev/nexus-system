#!/usr/bin/env bash
# Jarvis's secrets, encrypted with SOPS and age (README: Secrets). Plain settings stay in .env;
# API keys, passwords and tokens live in secrets/jarvis.sops.env, which only your age key opens.
#
#   ./scripts/secrets.sh init        create your age key, move the secrets out of .env into the
#                                    encrypted file and add TOKEN_ENCRYPTION_KEYS
#   ./scripts/secrets.sh edit        edit the secrets in $EDITOR; saved encrypted again
#   ./scripts/secrets.sh set NAME    set one secret, typed or piped in (stays out of history)
#   ./scripts/secrets.sh list        names of the stored secrets, no values
#
# Then start Jarvis with ./scripts/compose.sh (same arguments as docker compose).
# The key is at $SOPS_AGE_KEY_FILE (default ~/.config/jarvis/age.key). Keep a copy in your
# password manager: without it neither this file nor the copy in each backup can be opened.
set -euo pipefail
cd "$(dirname "$0")/.."

FILE=secrets/jarvis.sops.env
export SOPS_AGE_KEY_FILE=${SOPS_AGE_KEY_FILE:-$HOME/.config/jarvis/age.key}

# Everything in .env that is a credential. The rest of .env is plain settings.
SECRET_NAMES="OPENAI_API_KEY SECRET_KEY TOKEN_ENCRYPTION_KEYS POSTGRES_PASSWORD MINIO_ROOT_PASSWORD
LITELLM_MASTER_KEY LITELLM_API_KEY CLICKHOUSE_PASSWORD LANGFUSE_SALT LANGFUSE_ENCRYPTION_KEY
LANGFUSE_NEXTAUTH_SECRET LANGFUSE_SECRET_KEY LANGFUSE_ADMIN_PASSWORD OIDC_CLIENT_SECRET
DEV_LOGIN_TOKEN SANDBOX_TOKEN WORKBENCH_TOKEN TAVILY_API_KEY BRAVE_API_KEY SMTP_PASSWORD CLOUDFLARE_TUNNEL_TOKEN"

die() { echo "$*" >&2; exit 1; }
need() {
  command -v sops >/dev/null && command -v age-keygen >/dev/null \
    || die "sops and age aren't installed: brew install sops age"
}
is_secret() { [[ " $(echo $SECRET_NAMES) " == *" $1 "* ]]; }
pubkey() { age-keygen -y "$SOPS_AGE_KEY_FILE"; }

# Encrypt plain dotenv file $1 into $FILE, then check it decrypts back to the same text.
seal() {
  sops encrypt --filename-override "$FILE" --input-type dotenv --output-type dotenv "$1" \
    > "$FILE.new" || { rm -f "$FILE.new"; die "Couldn't encrypt the secrets."; }
  [[ "$(sops decrypt --input-type dotenv --output-type dotenv "$FILE.new")" == "$(cat "$1")" ]] \
    || { rm -f "$FILE.new"; die "The encrypted file didn't decrypt back to the same secrets."; }
  mv "$FILE.new" "$FILE"
}

cmd_init() {
  need
  [[ -f "$FILE" ]] && die "$FILE already exists. Use: $0 edit"
  [[ -f .env ]] || die "No .env yet. Run scripts/init-env.sh first."
  if [[ ! -f "$SOPS_AGE_KEY_FILE" ]]; then
    mkdir -p "$(dirname "$SOPS_AGE_KEY_FILE")"
    (umask 077 && age-keygen -o "$SOPS_AGE_KEY_FILE" 2>/dev/null)
    echo "Created your age key at $SOPS_AGE_KEY_FILE"
  fi
  printf 'creation_rules:\n  - path_regex: secrets/.*\\.sops\\.env$\n    age: %s\n' "$(pubkey)" > .sops.yaml

  umask 077
  plain=$(mktemp secrets/.plain.XXXXXX)
  rest=$(mktemp .env.XXXXXX)
  trap 'rm -f "$plain" "$rest"' EXIT
  while IFS= read -r line || [[ -n "$line" ]]; do
    name=${line%%=*}
    if [[ "$line" == *=* ]] && is_secret "$name"; then
      [[ -n "${line#*=}" ]] && echo "$line" >> "$plain"
    else
      echo "$line" >> "$rest"
    fi
  done < .env
  grep -q '^TOKEN_ENCRYPTION_KEYS=' "$plain" \
    || echo "TOKEN_ENCRYPTION_KEYS=$(openssl rand -hex 32)" >> "$plain"

  seal "$plain"
  { echo "# Secrets are in $FILE (./scripts/secrets.sh edit). Start with ./scripts/compose.sh."
    grep -v "^# Secrets are in $FILE" "$rest"; } > .env
  chmod 600 .env
  echo "Moved $(wc -l < "$plain" | tr -d ' ') secrets into $FILE; .env now has settings only."
  cat <<EOF

Next:
  1. Save your age key in your password manager (it's the one thing to keep safe):
       cat "$SOPS_AGE_KEY_FILE"
  2. Delete any plain copies of the old .env (for example jarvis-env.bak-*).
  3. Restart with the secrets loaded:  ./scripts/compose.sh up -d
EOF
}

cmd_edit() {
  need
  [[ -f "$FILE" ]] || die "No $FILE yet. Run: $0 init"
  sops edit "$FILE"
}

cmd_set() {
  need
  local name=${1:?usage: $0 set NAME}
  [[ "$name" =~ ^[A-Z_][A-Z0-9_]*$ ]] || die "$name isn't a valid name."
  [[ -f "$FILE" ]] || die "No $FILE yet. Run: $0 init"
  local value
  if [[ -t 0 ]]; then read -r -s -p "$name: " value; echo; else IFS= read -r value; fi
  [[ "$value" != *$'\n'* ]] || die "Values must be one line."
  umask 077
  plain=$(mktemp secrets/.plain.XXXXXX)
  trap 'rm -f "$plain"' EXIT
  sops decrypt "$FILE" | grep -v "^$name=" > "$plain" || true
  echo "$name=$value" >> "$plain"
  seal "$plain"
  echo "Saved $name. Apply it with: ./scripts/compose.sh up -d"
}

cmd_list() {
  need
  [[ -f "$FILE" ]] || die "No $FILE yet. Run: $0 init"
  sops decrypt "$FILE" | cut -d= -f1
}

case "${1:-}" in
  init) cmd_init ;;
  edit) cmd_edit ;;
  set) shift; cmd_set "$@" ;;
  list) cmd_list ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
