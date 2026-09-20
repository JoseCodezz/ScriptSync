"""Run a brand agent.

    python -m brand_agent --label labels/simvastatin.json --port 9001
    python -m brand_agent --label labels/clarithromycin.json --port 9002

--domain must be a domain the team controls (GoDaddy). Until one is registered,
the default `.example` placeholder keeps DNS verification honestly reporting
"not registered" instead of silently passing.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

from .service import build_app

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # allow `from scripts.build_label import ...`

from scripts.build_label import refresh_if_stale  # noqa: E402

# The label itself is only re-pulled from openFDA occasionally, not on every
# request or every process start: FDA labels change rarely, and hitting the
# API on every /answer would be both slow and unnecessary. Instead: check once
# at startup, then again every REFRESH_CHECK_HOURS for the life of the
# process, and only actually refetch when the cached copy is older than
# REFRESH_MAX_AGE_HOURS. brand_agent/service.py re-reads the label file on its
# own short TTL, so a refresh here reaches a running agent without a restart.
REFRESH_CHECK_HOURS = float(os.environ.get("FDA_REFRESH_CHECK_HOURS", "6"))
REFRESH_MAX_AGE_HOURS = float(os.environ.get("FDA_REFRESH_MAX_AGE_HOURS", "24"))


def _refresh_loop(drug: str) -> None:
    while True:
        time.sleep(REFRESH_CHECK_HOURS * 3600)
        if refresh_if_stale(drug, max_age_hours=REFRESH_MAX_AGE_HOURS):
            print(f"[{drug}] label refreshed from live openFDA", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a single-label brand agent")
    parser.add_argument("--label", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--domain", default=os.environ.get("ANS_DOMAIN", "scriptsync.health"))
    parser.add_argument("--version", default="v1.0.0")
    args = parser.parse_args()

    from .selector import selection_mode

    drug = args.label.stem
    if refresh_if_stale(drug, max_age_hours=REFRESH_MAX_AGE_HOURS):
        print(f"[{drug}] label refreshed from live openFDA (was stale)", flush=True)
    threading.Thread(target=_refresh_loop, args=(drug,), daemon=True).start()

    print(f"[{args.label.stem}] section selection: {selection_mode()}", flush=True)
    app = build_app(args.label, domain=args.domain, version=args.version)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
