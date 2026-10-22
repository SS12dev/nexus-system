#!/usr/bin/env bash
# Prove the workbench's network is boxed in. Starts egress-proxy, workbench, postgres and redis
# in a throwaway Compose project (jarvis-netcheck, removed at the end, with no published ports;
# your running Nexus is not touched), then from inside the workbench checks that:
#   - npm, PyPI and GitHub answer through the proxy,
#   - any other site is refused by the proxy,
#   - nothing is reachable directly: not postgres or redis (running, and reachable from the
#     default network, which is checked as a control), not the other Nexus services, not the
#     Mac, the LAN or the internet.
# Used by scripts/check.sh; run it alone after changing compose.yaml or infra/egress-proxy.
set -euo pipefail
cd "$(dirname "$0")/.."

# Publish no ports, so this runs next to a live Nexus that already holds 5432 and the rest.
override=$(mktemp)
printf 'services:\n  postgres:\n    ports: !reset []\n' > "$override"
dc() { docker compose -p jarvis-netcheck --env-file .env.example -f compose.yaml -f "$override" "$@"; }
trap 'dc down -v --remove-orphans >/dev/null 2>&1 || true; rm -f "$override"' EXIT

dc up -d --build --wait workbench postgres redis

echo "Control: redis is reachable from the default network"
dc exec -T postgres bash -c 'exec 3<>/dev/tcp/redis/6379' \
  || { echo "Control failed: the default network itself is broken" >&2; exit 1; }

# Their addresses too, in case a name doesn't resolve but the address would still answer.
ip() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$(dc ps -q "$1")"; }
PG_IP=$(ip postgres | awk '{print $1}')
REDIS_IP=$(ip redis | awk '{print $1}')

dc exec -T -e PG_IP="$PG_IP" -e REDIS_IP="$REDIS_IP" workbench python - <<'EOF'
import os
import socket
import sys
import urllib.error
import urllib.request

failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f' ({detail})' if detail else ''}")
    if not ok:
        failures.append(label)


def connects(host: str, port: int) -> str:
    try:
        socket.create_connection((host, port), timeout=3).close()
        return ""
    except OSError as exc:
        return f"{exc.__class__.__name__}: {exc}"


# Through the proxy (it is in HTTPS_PROXY): allowed hosts answer, others are refused.
for url in ("https://registry.npmjs.org/react", "https://pypi.org/simple/uv/", "https://github.com"):
    try:
        status = urllib.request.urlopen(url, timeout=20).status
        check(f"proxy allows {url}", status == 200, str(status))
    except Exception as exc:
        check(f"proxy allows {url}", False, repr(exc))
for url in ("https://example.com", "http://example.com", "https://postgres:5432", "https://1.1.1.1"):
    try:
        urllib.request.urlopen(url, timeout=20)
        check(f"proxy refuses {url}", False, "it answered")
    except (urllib.error.URLError, OSError) as exc:
        check(f"proxy refuses {url}", "403" in str(exc), str(exc)[:80])

# Directly, nothing: Nexus's services, the Mac, the LAN, the internet.
for host, port in [
    ("postgres", 5432), (os.environ["PG_IP"], 5432), ("redis", 6379),
    (os.environ["REDIS_IP"], 6379), ("minio", 9000), ("litellm", 4000), ("api", 8000),
    ("traefik", 80), ("langfuse-web", 3000), ("host.docker.internal", 11434),
    ("192.168.1.1", 80), ("10.0.0.1", 80), ("172.17.0.1", 80), ("1.1.1.1", 443),
    ("registry.npmjs.org", 443),
]:
    err = connects(host, port)
    check(f"no direct route to {host}:{port}", bool(err), err[:80])

sys.exit(1 if failures else 0)
EOF
echo "Workbench network checks passed."
