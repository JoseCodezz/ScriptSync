"""One command that runs the whole product: the label agents, the assistant, and the web UI on one URL.

    python scripts/serve.py                   # http://127.0.0.1:8080 on your machine
    PORT=10000 python scripts/serve.py        # what a host does: binds 0.0.0.0:$PORT
    SCRIPTSYNC_DEMO=1 python scripts/serve.py # also starts the four impostors and enables the presenter tools
    python scripts/serve.py --no-agents       # assistant + UI only (agents are hosted elsewhere)
    python scripts/serve.py --dry-run         # print what would start, start nothing

Hosting (Render, Railway, Fly, Heroku-style):  Build: pip install -r requirements.txt   Start: python scripts/serve.py
The platform sets PORT; the assistant serves the page, so there is nothing else to deploy.

The agents' private keys are not in git. On a host, provide each one as a secret env var
ANS_KEY_<DRUG>_PEM_B64 (base64 of keys/<drug>.ed25519), or the agent generates a fresh identity that DNS
does not publish and the identity checks will (correctly) fail.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # so `common` imports work when this file is run as a script

from common import agents_config, keys  # noqa: E402

KEY_DIR = keys.KEY_DIR
DOMAIN_IN_NAME = re.compile(r"\.v\d+\.\d+\.\d+\.(.+)$")


def load_config(path: Path | None = None) -> list[dict]:
    return agents_config.load_agents(path)   # applies SCRIPTSYNC_ENDPOINT_<DRUG> overrides


def is_local(endpoint: str) -> bool:
    return (urlparse(endpoint).hostname or "") in ("127.0.0.1", "localhost", "::1")


key_env_var = keys.key_env_var


def build_plan(agents: list[dict], demo: bool, python: str = sys.executable) -> list[dict]:
    """The child processes to start: {name, argv, port, health}. Agents hosted elsewhere are skipped."""
    plan = []
    for a in agents:
        if not is_local(a["endpoint"]):
            continue
        port = urlparse(a["endpoint"]).port
        if a.get("role") == "attacker":
            if not demo:
                continue
            plan.append({"name": f"impostor-{a['attack']}", "port": port, "health": None,
                         "argv": [python, "-m", "dev.mock_agent", "--port", str(port), "--which", "A",
                                  "--mode", a["attack"], "--name", a["ansName"]]})
        else:
            m = DOMAIN_IN_NAME.search(a["ansName"])
            domain = m.group(1) if m else os.environ.get("ANS_DOMAIN", "scriptsync.health")
            plan.append({"name": f"agent-{a['drug']}", "port": port, "health": f"http://127.0.0.1:{port}/health",
                         "argv": [python, "-m", "brand_agent", "--label", f"labels/{a['drug']}.json",
                                  "--port", str(port), "--domain", domain]})
    return plan


def provision_keys(agents: list[dict], environ=os.environ, key_dir: Path = KEY_DIR) -> list[str]:
    """Write agent private keys from env vars (hosts have no key files). Never overwrites an existing key."""
    written = []
    for a in agents:
        if a.get("role") == "attacker" or not is_local(a["endpoint"]):
            continue
        if keys.provision_key(a["drug"], environ, key_dir):
            written.append(keys.key_path(a["drug"], key_dir).name)
    return written


def wait_for(item: dict, proc: subprocess.Popen, timeout: float = 45.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if item["health"]:
                httpx.get(item["health"], timeout=2).raise_for_status()
            else:
                socket.create_connection(("127.0.0.1", item["port"]), timeout=1).close()
            return True
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.4)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ScriptSync: agents + assistant + web UI")
    parser.add_argument("--host", help="bind address (default 127.0.0.1, or 0.0.0.0 when PORT is set)")
    parser.add_argument("--port", type=int, help="port (default $PORT, else 8080)")
    parser.add_argument("--no-agents", action="store_true", help="do not start local agents")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = parser.parse_args(argv)

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        print("WARNING: python-dotenv is not installed, so .env was NOT loaded.", flush=True)

    hosted = "PORT" in os.environ
    host = args.host or os.environ.get("SCRIPTSYNC_HOST") or ("0.0.0.0" if hosted else "127.0.0.1")
    port = args.port or int(os.environ.get("PORT", "8080"))
    demo = os.environ.get("SCRIPTSYNC_DEMO") == "1"

    agents = load_config()
    plan = [] if args.no_agents else build_plan(agents, demo)

    if args.dry_run:
        print(json.dumps({"assistant": {"host": host, "port": port, "demo": demo},
                          "children": [{"name": p["name"], "argv": p["argv"][1:]} for p in plan]}, indent=2))
        return 0

    for name in provision_keys(agents):
        print(f"wrote agent key {name} from the environment", flush=True)
    for a in agents:
        if a.get("role") != "attacker" and is_local(a["endpoint"]) and not args.no_agents \
                and not keys.key_path(a["drug"]).exists():
            print(f"WARNING: no key for the {a['drug']} agent. It will generate a new identity that DNS does not "
                  f"publish, so its identity check will fail. Set {key_env_var(a['drug'])}.", flush=True)

    children: list[subprocess.Popen] = []
    try:
        started = []
        for item in plan:
            children.append(subprocess.Popen(item["argv"], cwd=ROOT))
            started.append((item, children[-1]))
        for item, proc in started:
            ok = wait_for(item, proc)
            print(f"  {item['name']:<28} port {item['port']}  {'up' if ok else 'NOT UP (see its log above)'}", flush=True)

        import uvicorn
        print(f"ScriptSync ({'demo' if demo else 'product'} mode) on http://{host}:{port}", flush=True)
        uvicorn.run("assistant.server:app", host=host, port=port, log_level="info")
        return 0
    finally:
        for proc in children:
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
