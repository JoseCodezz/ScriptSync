#!/usr/bin/env bash
# Start both brand agents. Ctrl-C stops them.
#   ANS_DOMAIN=yourdomain.com ./scripts/run_brand_agents.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
DOMAIN=${ANS_DOMAIN:-scriptsync.health}

$PY -m brand_agent --label labels/simvastatin.json    --port 9001 --domain "$DOMAIN" &
$PY -m brand_agent --label labels/clarithromycin.json --port 9002 --domain "$DOMAIN" &
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM

for port in 9001 9002; do
  until curl -sf "http://127.0.0.1:$port/health" >/dev/null; do sleep 0.3; done
done
echo "simvastatin    -> http://127.0.0.1:9001  (domain: $DOMAIN)"
echo "clarithromycin -> http://127.0.0.1:9002"
echo "Ctrl-C to stop."
wait
