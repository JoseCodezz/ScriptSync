#!/usr/bin/env bash
# One command to confirm the demo is ready. Safe to run repeatedly.
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
[ -f .env ] && set -a && . ./.env && set +a

echo "ScriptSync preflight"
echo "--------------------"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "  API key .......... MISSING - agents will use keyword fallback"
  echo "                     cp .env.example .env and add ANTHROPIC_API_KEY"
elif [ "${ANTHROPIC_API_KEY#sk-ant-}" = "$ANTHROPIC_API_KEY" ] || [ ${#ANTHROPIC_API_KEY} -lt 90 ]; then
  echo "  API key .......... MALFORMED (${#ANTHROPIC_API_KEY} chars, expected sk-ant-... ~100+)"
  echo "                     that looks like a key NAME, not the secret value"
else
  echo "  API key .......... set (${#ANTHROPIC_API_KEY} chars)"
fi
"$PY" -c "import dotenv" 2>/dev/null || echo "  python-dotenv .... MISSING - .env will NOT be loaded"

for port in 9001 9002 8080; do
  if curl -sf --max-time 3 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo "  port $port ........ up"
  else
    echo "  port $port ........ DOWN"
  fi
done

mode=$(curl -sf --max-time 3 http://127.0.0.1:9001/health 2>/dev/null \
       | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('selection','?'))" 2>/dev/null)
[ -n "$mode" ] && echo "  selection ........ $mode"

echo "  ANS identity ....."
"$PY" scripts/ans_verify_live.py 2>/dev/null \
  | grep -E "VERIFIED|LOOKUP FAILED|agents verified" | sed 's/^/     /'
