"""ANS identity for a brand agent.

The trust chain, strongest link last:

    1. The agent holds an Ed25519 private key nobody else has.
    2. It serves its public key at GET /identity, and signs a challenge on
       demand at POST /ans/challenge - so a verifier can prove the agent holds
       the private key *right now*, not that it replayed an old signature.
    3. A TXT record on a domain the team controls pins that key's fingerprint.

Step 3 is the GoDaddy piece and the only one still waiting on a domain.

This module implements the ans-verify wire protocol exactly. All four of these
must match or verification silently fails:

    TXT host    _agentid.<agent>.<domain>        (dns.ts recordNameFor)
    TXT value   "v=agentkey1; k=<base64 SPKI>"   (dns.ts publishAgentTxtRecord)
    key format  base64 SPKI DER                  (agentKeys.ts)
    signed msg  agent-identity-v1|domain|agent|challenge|issuedAt
                                                 (verifyAgent.ts buildSigningPayload)

The full public key lives in DNS, not a fingerprint of it. That is the stronger
design: a third-party verifier needs nothing but a public DNS lookup and never
has to contact - or trust - the agent itself.
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
    def dns_label(self) -> str:
        """The single DNS label naming this agent, e.g. "simvastatin".

        ans-verify validates this as /^[a-zA-Z0-9-]{1,63}$/ - it is the `agent`
        field in every challenge/verify call, NOT the full a2a:// name.
        """
        return self.provider

    @property
    def txt_record(self) -> str:
        """FQDN of the TXT record holding this agent's public key."""
        return f"_agentid.{self.dns_label}.{self.domain}"

    @property
    def txt_host(self) -> str:
        """The host field as a registrar's DNS panel wants it (no domain suffix)."""
        return f"_agentid.{self.dns_label}"


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

    def sign_payload(self, payload: str) -> str:
        return base64.b64encode(self._private_key.sign(payload.encode("utf-8"))).decode("ascii")

    def sign_challenge(self, domain: str, agent: str, challenge: str, issued_at: int) -> str:
        """Sign an ans-verify challenge round.

        The payload is rebuilt here from its components rather than signing a
        string handed to us. Blind-signing whatever a caller sends would let
        that caller obtain a signature over text of their choosing.
        """
        return self.sign_payload(signing_payload(domain, agent, challenge, issued_at))

    def txt_value(self) -> str:
        """Exactly what to paste into the DNS TXT record."""
        return f"v=agentkey1; k={self.public_key_b64}"


def signing_payload(domain: str, agent: str, challenge: str, issued_at: int) -> str:
    """The canonical string both sides must build identically.

    Mirrors buildSigningPayload() in ans-verify/src/crypto/verifyAgent.ts. Any
    difference here - field order, separator, stringified number - produces a
    signature that fails to verify with no useful error.
    """
    return f"agent-identity-v1|{domain}|{agent}|{challenge}|{issued_at}"


def verify_payload(payload: str, signature_b64: str, public_key_b64: str) -> bool:
    """Check a signature over an exact payload string, given a base64 SPKI key."""
    try:
        key = serialization.load_der_public_key(base64.b64decode(public_key_b64))
        if not isinstance(key, Ed25519PublicKey):
            return False
        key.verify(base64.b64decode(signature_b64), payload.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify_challenge(
    domain: str, agent: str, challenge: str, issued_at: int,
    signature_b64: str, public_key_b64: str,
) -> bool:
    return verify_payload(
        signing_payload(domain, agent, challenge, issued_at), signature_b64, public_key_b64
    )


def parse_public_key_from_txt(txt_value: str) -> str | None:
    """Pull the base64 key out of "v=agentkey1; k=<base64>" (dns.ts parser)."""
    match = re.search(r"k=([A-Za-z0-9+/=_-]+)", txt_value)
    return match.group(1) if match else None


def check_dns_anchor(ans_name: ANSName, public_key_b64: str) -> tuple[bool | None, str]:
    """Resolve the agent's TXT record and confirm it publishes this exact key.

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

    for record in answers:
        value = b"".join(record.strings).decode("utf-8", errors="replace")
        published = parse_public_key_from_txt(value)
        if published == public_key_b64:
            return True, f"Public key published at {ans_name.txt_record}"
        if published:
            return False, (
                f"TXT record at {ans_name.txt_record} publishes a DIFFERENT key "
                "than this agent holds"
            )
    return False, f"No v=agentkey1 TXT record found at {ans_name.txt_record}"
