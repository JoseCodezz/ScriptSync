"""Run a brand agent.

    python -m brand_agent --label labels/simvastatin.json --port 9001
    python -m brand_agent --label labels/clarithromycin.json --port 9002

--domain must be a domain the team controls (GoDaddy). Until one is registered,
the default `.example` placeholder keeps DNS verification honestly reporting
"not registered" instead of silently passing.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import uvicorn

from common.keys import key_env_var, provision_key

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
    # A host has no key files: take this agent's key from ANS_KEY_<DRUG>_PEM_B64 before it loads its identity.
    drug = json.loads(args.label.read_text(encoding="utf-8"))["agent"]["drug"]
    if provision_key(drug):
        print(f"[{args.label.stem}] wrote its key from {key_env_var(drug)}", flush=True)
    app = build_app(args.label, domain=args.domain, version=args.version)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
