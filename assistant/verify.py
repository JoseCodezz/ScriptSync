"""Identity verification (F3).

isAgentVerified(name) is the ONLY place the app talks to ANS. Right now it is
SIMULATED (Stage A): results come from config/agents.json `identity_stub`.
Going live/cached (Stage B/C) means replacing the body of is_agent_verified;
nothing else in the project changes as long as the return shape stays the same.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODE = "simulated"  # "simulated" | "cached" | "live"

CHECK_IDS = ["dns", "cert", "log", "current"]
DEFAULT_MESSAGES = {
    "dns": ("Domain record found", "No domain record found for this agent"),
    "cert": ("Certificate valid, matches name", "Certificate missing, expired, or not matching the name"),
    "log": ("Listed in the public registration log", "Not found in the public registration log"),
    "current": ("Not revoked; this is the current version", "Identity revoked or replaced by a newer version"),
}


def _load_agents():
    with open(ROOT / "config" / "agents.json", encoding="utf-8") as f:
        return json.load(f)["agents"]


def is_agent_verified(name: str) -> dict:
    """Return {name, ok, mode, checks:[{id, pass, message}], warnings:[...]}."""
    agent = next((a for a in _load_agents() if a["ansName"] == name), None)
    checks = []
    if agent is None:
        # Unknown name: nothing registered anywhere.
        for cid in CHECK_IDS:
            checks.append({"id": cid, "pass": False, "message": DEFAULT_MESSAGES[cid][1]})
        return {"name": name, "ok": False, "mode": MODE, "checks": checks, "warnings": []}

    stub = agent.get("identity_stub", {})
    for cid in CHECK_IDS:
        entry = stub.get(cid, {"pass": True})
        ok_msg, bad_msg = DEFAULT_MESSAGES[cid]
        passed = bool(entry.get("pass", True))
        checks.append({
            "id": cid,
            "pass": passed,
            "message": entry.get("message", ok_msg if passed else bad_msg),
        })
    return {
        "name": name,
        "ok": all(c["pass"] for c in checks),
        "mode": MODE,
        "checks": checks,
        "warnings": agent.get("warnings", []),
    }


isAgentVerified = is_agent_verified  # name used in the build document
