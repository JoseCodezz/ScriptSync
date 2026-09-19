"""ANS identity for a brand agent.

The trust chain, strongest link last:

    1. The agent holds an Ed25519 private key nobody else has.
    2. It serves its public key at GET /identity, and signs a challenge on
       demand at POST /ans/challenge - so a verifier can prove the agent holds
       the private key *right now*, not that it replayed an old signature.
    3. A TXT record on a domain the team controls pins that key's fingerprint.

Step 3 is the GoDaddy piece and the only one still waiting on a domain.

Key encoding is base64 SPKI DER, matching `generateAgentKeyPair()` in
ans-verify/src/crypto/agentKeys.ts, so keys minted by either side verify against
the other. (Raw 32-byte encoding would NOT interoperate - it was the mismatch
worth catching before both halves were written.)
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

KEY_DIR = Path(__file__).resolve().parent.parent / "keys"

# a2a://labelAgent.drugInfo.<drug>.v<MAJOR>.<MINOR>.<PATCH>.<domain>
ANS_PATTERN = re.compile(
    r"^(?P<protocol>[a-z0-9]+)://"
    r"(?P<agent_id>[A-Za-z0-9-]+)\."
    r"(?P<capability>[A-Za-z0-9-]+)\."
    r"(?P<provider>[A-Za-z0-9-]+)\."
    r"(?P<version>v\d+\.\d+\.\d+)\."
    r"(?P<domain>[A-Za-z0-9.-]+)$"
)


@dataclass(frozen=True)
class ANSName:
    """A parsed ANS name. The domain suffix is what DNS verification hangs off."""

    protocol: str
    agent_id: str
    capability: str
    provider: str
    version: str
    domain: str
    full: str

    @classmethod
    def parse(cls, name: str) -> "ANSName":
        match = ANS_PATTERN.match(name)
        if not match:
            raise ValueError(
                f"Malformed ANS name {name!r}. Expected "
                "protocol://agentId.capability.provider.vX.Y.Z.domain"
            )
        return cls(**match.groupdict(), full=name)

    @classmethod
    def build(cls, drug: str, domain: str, version: str = "v1.0.0") -> "ANSName":
        return cls.parse(f"a2a://labelAgent.drugInfo.{drug}.{version}.{domain}")

    @property
    def txt_record(self) -> str:
        """FQDN of the TXT record that pins this agent's key."""
        return f"_ans.{self.provider}.{self.domain}"


class AgentIdentity:
    """The agent's signing key, plus the values a verifier needs to check it."""

    def __init__(self, private_key: Ed25519PrivateKey, ans_name: ANSName) -> None:
        self._private_key = private_key
        self.ans_name = ans_name

    @classmethod
    def load_or_create(cls, ans_name: ANSName, key_dir: Path | None = None) -> "AgentIdentity":
        """Read this agent's key from disk, generating one on first run.

        Demo convenience only - a real deployment keeps this in a KMS and the
        private key never touches a filesystem.
        """
        directory = key_dir or KEY_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{ans_name.provider}.ed25519"

        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise TypeError(f"{path} is not an Ed25519 private key")
        else:
            key = Ed25519PrivateKey.generate()
            path.write_bytes(
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            path.chmod(0o600)
        return cls(key, ans_name)

    @property
    def public_key_b64(self) -> str:
        """Base64 SPKI DER - the same encoding ans-verify publishes in DNS."""
        return base64.b64encode(
            self._private_key.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode("ascii")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(base64.b64decode(self.public_key_b64)).hexdigest()

    def sign_challenge(self, challenge: str) -> str:
        """Prove key possession right now. Verifier supplies a fresh nonce."""
        return base64.b64encode(
            self._private_key.sign(challenge.encode("utf-8"))
        ).decode("ascii")

    def txt_value(self) -> str:
        """Exactly what to paste into the GoDaddy TXT record."""
        return (
            f"v=ans1; name={self.ans_name.full}; alg=ed25519; "
            f"key=sha256:{self.fingerprint}"
        )


def verify_challenge(challenge: str, signature_b64: str, public_key_b64: str) -> bool:
    """Check a challenge signature against a base64 SPKI DER public key."""
    try:
        key = serialization.load_der_public_key(base64.b64decode(public_key_b64))
        if not isinstance(key, Ed25519PublicKey):
            return False
        key.verify(base64.b64decode(signature_b64), challenge.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def check_dns_anchor(ans_name: ANSName, fingerprint: str) -> tuple[bool | None, str]:
    """Resolve the agent's TXT record and confirm it pins this key.

    Returns (anchored, detail). None means the check was not attempted - either
    ANS_DNS_VERIFY is off or the name still uses a placeholder domain. None is
    "unknown", never "fine": callers must not treat it as a pass.
    """
    if os.environ.get("ANS_DNS_VERIFY", "0") != "1":
        return None, "DNS verification disabled (set ANS_DNS_VERIFY=1)"
    if ans_name.domain.endswith(".example") or ans_name.domain == "example":
        return None, f"{ans_name.domain} is a placeholder; no domain registered yet"

    try:
        import dns.resolver
    except ImportError:
        return None, "dnspython not installed"

    try:
        answers = dns.resolver.resolve(ans_name.txt_record, "TXT")
    except Exception as exc:  # noqa: BLE001 - any DNS failure is a failed check
        return False, f"TXT lookup for {ans_name.txt_record} failed: {exc}"

    expected = f"key=sha256:{fingerprint}"
    for record in answers:
        value = b"".join(record.strings).decode("utf-8", errors="replace")
        if expected in value:
            return True, f"Key pinned by TXT record at {ans_name.txt_record}"
    return False, f"TXT record at {ans_name.txt_record} does not pin this key"
