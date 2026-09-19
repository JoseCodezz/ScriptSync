"""Run a drug agent.

    python -m agents.drug_agent --data data/company_a.json --port 8001
    python -m agents.drug_agent --data data/company_b.json --port 8002
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .service import build_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a single-product drug information agent")
    parser.add_argument("--data", required=True, type=Path, help="Path to the monograph JSON")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Publicly reachable base URL for this agent (default: http://host:port)",
    )
    parser.add_argument(
        "--dns-domain",
        default=None,
        help=(
            "Domain you control, e.g. scriptsync.dev. The agent will publish the TXT record "
            "callers should verify against. Omit while running on localhost."
        ),
    )
    args = parser.parse_args()

    endpoint = args.endpoint or f"http://{args.host}:{args.port}"
    app = build_app(args.data, endpoint=endpoint, dns_domain=args.dns_domain)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
