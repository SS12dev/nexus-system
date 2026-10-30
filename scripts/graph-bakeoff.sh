#!/usr/bin/env bash
# Tests which model should build the knowledge graph, on one account's own chats, and copies
# the report to ./graph-bakeoff/. Local models must be pulled first (`ollama pull <tag>`).
#   ./scripts/graph-bakeoff.sh someone@example.com --models qwen3.5:9b,gemma4:12b,nexus-nano
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { echo "usage: $0 someone@example.com [--models a,b] [--chats 50]" >&2; exit 2; }
./scripts/compose.sh exec -T orchestrator rm -rf /tmp/graph-bakeoff
./scripts/compose.sh exec -T orchestrator python -m nexus_orchestrator.graph_bakeoff "$@" --out /tmp/graph-bakeoff
mkdir -p graph-bakeoff
./scripts/compose.sh cp orchestrator:/tmp/graph-bakeoff/. graph-bakeoff/
echo "Copied to graph-bakeoff/ (newest folder holds report.md)"
