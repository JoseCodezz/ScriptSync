#!/usr/bin/env bash
# Serve the web UI on 5500. It calls the assistant at 127.0.0.1:8080.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-$PWD/.venv/bin/python}   # absolute: --directory changes the cwd
exec "$PY" -m http.server 5500 --bind 127.0.0.1 --directory web
