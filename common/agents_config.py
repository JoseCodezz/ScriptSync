"""The one place config/agents.json is read.

A hosted site must point at public agent URLs without editing tracked config, so each label agent's
endpoint can be overridden from the environment:

    SCRIPTSYNC_ENDPOINT_SIMVASTATIN=https://agent.scriptsync.health
    SCRIPTSYNC_ENDPOINT_CLARITHROMYCIN=https://agentb.scriptsync.health

Everything that needs the agent list (the assistant, identity verification, the start script) goes
through load_agents(), so they can never disagree about where an agent lives.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "agents.json"


def endpoint_env_var(drug: str) -> str:
    return "SCRIPTSYNC_ENDPOINT_" + re.sub(r"[^A-Z0-9]", "_", drug.upper())


def load_agents(path: Path | None = None, environ=os.environ) -> list[dict]:
    with open(path or CONFIG, encoding="utf-8") as f:
        agents = json.load(f)["agents"]
    for agent in agents:
        if agent.get("role") == "attacker" or not agent.get("drug"):
            continue  # impostors always stay local; only real label agents can be relocated
        override = (environ.get(endpoint_env_var(agent["drug"])) or "").strip()
        if override:
            agent["endpoint"] = override.rstrip("/")
    return agents
