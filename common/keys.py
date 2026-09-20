"""Agent private keys from the environment.

The agents' Ed25519 keys (keys/<drug>.ed25519) are not in git. A host has no key files, so each key can be
supplied as a secret env var instead: ANS_KEY_<DRUG>_PEM_B64 = base64 of the PEM file. The key is written to
keys/ before the agent starts, so the agent keeps the identity DNS publishes rather than generating a new one.
"""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_DIR = ROOT / "keys"


def key_env_var(drug: str) -> str:
    return "ANS_KEY_" + re.sub(r"[^A-Z0-9]", "_", drug.upper()) + "_PEM_B64"


def key_path(drug: str, key_dir: Path = KEY_DIR) -> Path:
    return key_dir / f"{drug}.ed25519"


def provision_key(drug: str, environ=os.environ, key_dir: Path = KEY_DIR) -> bool:
    """Write this agent's key from its env var. Never overwrites an existing key. True if a key was written."""
    value = (environ.get(key_env_var(drug)) or "").strip()
    target = key_path(drug, key_dir)
    if not value or target.exists():
        return False
    key_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(value))
    try:
        target.chmod(0o600)
    except OSError:
        pass  # not supported on every filesystem
    return True
