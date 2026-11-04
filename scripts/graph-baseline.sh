#!/usr/bin/env bash
# Runs the real graph extraction on a sample of one account's imported conversations with the
# local model and cloud models side by side, scored against the strongest one, and copies the
# report to ./graph-baseline/. Nothing is written to the graph. Cloud calls cost money: the
# report says how much, and what re-reading every imported conversation would cost.
#   ./scripts/graph-baseline.sh someone@example.com --reference nexus-max --models qwen3.5:9b,nexus-pro --chats 40
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { echo "usage: $0 someone@example.com [--reference nexus-max] [--models a,b] [--chats 40]" >&2; exit 2; }
./scripts/compose.sh exec -T orchestrator rm -rf /tmp/graph-baseline
./scripts/compose.sh exec -T orchestrator python -m nexus_orchestrator.graph_baseline "$@" --out /tmp/graph-baseline
mkdir -p graph-baseline
./scripts/compose.sh cp orchestrator:/tmp/graph-baseline/. graph-baseline/
echo "Copied to graph-baseline/ (newest folder holds report.md and chats.json)"
