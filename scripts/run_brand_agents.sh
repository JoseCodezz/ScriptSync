#!/usr/bin/env bash
# Start both brand agents. Ctrl-C stops them.
#
#   ./scripts/run_brand_agents.sh              # start
#   ./scripts/run_brand_agents.sh --restart    # reclaim ports from stale agents
#
# Reads .env for ANTHROPIC_API_KEY and ANS_* settings.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-.venv/bin/python}
DOMAIN=${ANS_DOMAIN:-scriptsync.health}
RESTART=${1:-}

# Ports held by a previous run are the most common startup failure - uvicorn
# reports "address already in use" and exits, which reads like a code error.
STALE=""
for port in 9001 9002; do
  pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  [ -n "$pids" ] && STALE="$STALE $port:$pids"
done

if [ -n "$STALE" ]; then
  if [ "$RESTART" = "--restart" ]; then
    echo "Reclaiming ports:$STALE"
    for port in 9001 9002; do
      pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
      [ -n "$pids" ] && kill $pids 2>/dev/null || true
    done
    sleep 1
  else
    echo "Ports already in use:$STALE" >&2
    echo "Agents are probably still running from an earlier start." >&2
    echo "Re-run with --restart to replace them, or just use the ones already up." >&2
    exit 1
  fi
fi

$PY -m brand_agent --label labels/simvastatin.json    --port 9001 --domain "$DOMAIN" &
$PY -m brand_agent --label labels/clarithromycin.json --port 9002 --domain "$DOMAIN" &
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM

for port in 9001 9002; do
  for _ in $(seq 60); do
    curl -sf "http://127.0.0.1:$port/health" >/dev/null && break
    sleep 0.3
  done
done

echo "simvastatin    -> http://127.0.0.1:9001  (domain: $DOMAIN)"
echo "clarithromycin -> http://127.0.0.1:9002"
curl -s http://127.0.0.1:9001/health | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
print(f\"section selection: {d.get('selection','?')}\")" 2>/dev/null || true
echo "Ctrl-C to stop."
wait
