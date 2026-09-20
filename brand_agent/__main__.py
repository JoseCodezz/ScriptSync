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
    # A host (Railway, Render, Fly) assigns the port through $PORT and expects
    # the process to bind 0.0.0.0. Locally both default to loopback.
    parser.add_argument("--host", default=os.environ.get("HOST") or
                        ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ["PORT"]) if os.environ.get("PORT") else None)
    parser.add_argument("--domain", default=os.environ.get("ANS_DOMAIN", "scriptsync.health"))
    parser.add_argument("--version", default="v1.0.0")
    args = parser.parse_args()

    from .selector import selection_mode

    print(f"[{args.label.stem}] section selection: {selection_mode()}", flush=True)
    app = build_app(args.label, domain=args.domain, version=args.version)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
