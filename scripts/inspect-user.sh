#!/usr/bin/env bash
# What Nexus holds for one account (setup answers, memories, graph, routines and recent runs),
# read-only, from the running API container: ./scripts/inspect-user.sh someone@example.com
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -eq 1 ] || { echo "usage: $0 someone@example.com" >&2; exit 2; }
exec ./scripts/compose.sh exec -T api python -m nexus_api.inspect_user "$1"
