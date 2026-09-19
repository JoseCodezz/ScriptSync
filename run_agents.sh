#!/usr/bin/env bash
# Start both drug agents in the background. Ctrl-C stops them.
set -euo pipefail

cd "$(dirname "$0")"

python -m agents.drug_agent --data data/company_a.json --port 8001 &
A_PID=$!
python -m agents.drug_agent --data data/company_b.json --port 8002 &
B_PID=$!

cleanup() { kill "$A_PID" "$B_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "Renavex agent  -> http://127.0.0.1:8001"
echo "Glyvera agent  -> http://127.0.0.1:8002"
echo
echo "Waiting for both to come up..."
for port in 8001 8002; do
  until curl -sf "http://127.0.0.1:$port/health" >/dev/null; do sleep 0.3; done
done
echo "Both agents ready. Ctrl-C to stop."

wait
