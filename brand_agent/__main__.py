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
from pathlib import Path

import uvicorn

from .service import build_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a single-label brand agent")
    parser.add_argument("--label", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--domain", default=os.environ.get("ANS_DOMAIN", "scriptsync.health"))
    parser.add_argument("--version", default="v1.0.0")
    args = parser.parse_args()

    from .selector import selection_mode

    print(f"[{args.label.stem}] section selection: {selection_mode()}", flush=True)
    app = build_app(args.label, domain=args.domain, version=args.version)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
