#!/usr/bin/env bash
# Look for known vulnerabilities in the packages Nexus ships: every Python package in uv.lock
# (dev tools left out) against the PyPI advisory database, and the web app's npm packages.
# Needs uv and npm, and network access. check.sh runs it; on its own:
#
#   ./scripts/audit.sh
#
# A finding fails it. The fix is nearly always a newer version:
#   uv lock --upgrade-package <name>          (then rebuild the images)
#   (cd apps/web && npm update <name>)
# If no fixed version exists yet and the finding doesn't apply to Nexus, skip it with
# AUDIT_IGNORE="<id> <id>" ./scripts/audit.sh and note why in the PR.
set -euo pipefail
cd "$(dirname "$0")/.."

req=$(mktemp)
trap 'rm -f "$req"' EXIT
uv export --frozen --all-packages --no-hashes --no-emit-workspace --no-dev -q -o "$req"

ignore=()
for id in ${AUDIT_IGNORE:-}; do ignore+=(--ignore-vuln "$id"); done

echo "Python packages (uv.lock):"
# --no-deps: the export already lists every package at its locked version.
set +e
uvx pip-audit -r "$req" --no-deps --disable-pip --progress-spinner off "${ignore[@]}" 2>&1 \
  | grep -v -e '^WARNING:pip_audit' -e 'UV_NATIVE_TLS'
status=${PIPESTATUS[0]}
set -e

echo "Web app packages (apps/web, production only):"
(cd apps/web && npm audit --omit=dev --audit-level=high) || status=1

exit "$status"
