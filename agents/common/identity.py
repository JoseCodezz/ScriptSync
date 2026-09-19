"""Agent identity: Ed25519 keypairs, response signing, and ANS name parsing.

The trust chain this sets up, in order of strength:

    1. The agent signs every answer with a private key it alone holds.
    2. The agent publishes the matching public key in its agent card.
    3. A TXT record in DNS - a zone the provider demonstrably controls -
       pins the fingerprint of that key.

Step 3 is what turns "some server said it was Corvus Therapeutics" into
"whoever controls corvus-therapeutics.com says this key speaks for them."
That is the piece GoDaddy DNS provides.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

KEY_DIR = Path(__file__).resolve().parents[2] / "keys"


def canonical_json(payload: dict) -> bytes:
    """Deterministic bytes for a dict, so both sides sign the same thing.

    Sorted keys, no incidental whitespace. Any disagreement here shows up as a
    signature failure, which is confusing to debug - so it lives in one place.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ANSName:
    """A parsed ANS name: protocol://AgentID.Capability.Provider.vMAJOR.MINOR.PATCH"""

    protocol: str
    agent_id: str
    capability: str
    provider: str
    version: str

    @property
    def full(self) -> str:
        return f"{self.protocol}://{self.agent_id}.{self.capability}.{self.provider}.{self.version}"

    @classmethod
    def parse(cls, name: str) -> "ANSName":
        if "://" not in name:
            raise ValueError(f"ANS name missing protocol scheme: {name!r}")
        protocol, remainder = name.split("://", 1)
        parts = remainder.split(".")
        # Version is the trailing vX.Y.Z - three dot-separated segments.
        if len(parts) < 5 or not parts[-3].startswith("v"):
            raise ValueError(
                f"ANS name must end in vMAJOR.MINOR.PATCH: {name!r}"
            )
        version = ".".join(parts[-3:])
        agent_id, capability = parts[0], parts[1]
        provider = ".".join(parts[2:-3])
        if not provider:
            raise ValueError(f"ANS name missing provider segment: {name!r}")
        return cls(protocol, agent_id, capability, provider, version)


class AgentIdentity:
    """An agent's signing key and the fingerprint others use to pin it."""

    def __init__(self, private_key: Ed25519PrivateKey, ans_name: str) -> None:
        self._private_key = private_key
        self.ans_name = ans_name

    @classmethod
    def load_or_create(cls, ans_name: str, key_dir: Path | None = None) -> "AgentIdentity":
        """Read the agent's key from disk, generating one on first run.

        Demo convenience. A real deployment holds this in a KMS or an HSM and
        never lets it touch a filesystem.
        """
        directory = key_dir or KEY_DIR
        directory.mkdir(parents=True, exist_ok=True)
        slug = ANSName.parse(ans_name).agent_id
        key_path = directory / f"{slug}.ed25519"

        if key_path.exists():
            private_key = serialization.load_pem_private_key(
                key_path.read_bytes(), password=None
            )
            if not isinstance(private_key, Ed25519PrivateKey):
                raise TypeError(f"{key_path} is not an Ed25519 private key")
        else:
            private_key = Ed25519PrivateKey.generate()
            key_path.write_bytes(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            key_path.chmod(0o600)

        return cls(private_key, ans_name)

    @property
    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key_bytes).decode("ascii")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.public_key_bytes).hexdigest()

    def sign(self, payload: dict) -> str:
        return base64.b64encode(self._private_key.sign(canonical_json(payload))).decode("ascii")

    def dns_txt_value(self) -> str:
        """The exact string to paste into a GoDaddy TXT record for this agent."""
        return f"v=ans1; name={self.ans_name}; alg=ed25519; key=sha256:{self.fingerprint}"


def verify_signature(payload: dict, signature_b64: str, public_key_b64: str) -> bool:
    """Check that `signature_b64` covers `payload` under `public_key_b64`."""
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        public_key.verify(base64.b64decode(signature_b64), canonical_json(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def fingerprint_of(public_key_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()
