"""Name resolution: ANS name -> endpoint, plus DNS-anchored trust checks.

This is the seam ANS slots into. Today `resolve()` reads a local JSON file.
When the real registry exists, point ANS_RESOLVER_URL at it and the rest of the
system does not change - the HCP assistant only ever asks for a name.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from .identity import ANSName, fingerprint_of
from .schemas import AgentCard, VerificationResult, SignedEnvelope
from .identity import verify_signature

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "registry.json"


class ResolutionError(RuntimeError):
    """Raised when a name cannot be turned into a reachable endpoint."""


def _local_registry() -> dict[str, str]:
    if not REGISTRY_PATH.exists():
        return {}
    return json.loads(REGISTRY_PATH.read_text())


def resolve(ans_name: str) -> str:
    """Return the base URL for an ANS name.

    Resolution order:
      1. ANS_RESOLVER_URL, if set - the real registry, once it exists.
      2. agents/registry.json - the local stand-in.
    """
    ANSName.parse(ans_name)  # fail fast on a malformed name

    resolver_url = os.environ.get("ANS_RESOLVER_URL")
    if resolver_url:
        response = httpx.get(
            f"{resolver_url.rstrip('/')}/resolve",
            params={"name": ans_name},
            timeout=10.0,
        )
        response.raise_for_status()
        endpoint = response.json().get("endpoint")
        if not endpoint:
            raise ResolutionError(f"Resolver returned no endpoint for {ans_name}")
        return endpoint.rstrip("/")

    registry = _local_registry()
    if ans_name not in registry:
        known = ", ".join(registry) or "(registry empty)"
        raise ResolutionError(f"{ans_name} not in local registry. Known: {known}")
    return registry[ans_name].rstrip("/")


def known_agents() -> list[str]:
    return list(_local_registry())


async def fetch_agent_card(endpoint: str, client: httpx.AsyncClient) -> AgentCard:
    response = await client.get(f"{endpoint}/.well-known/agent.json", timeout=10.0)
    response.raise_for_status()
    return AgentCard.model_validate(response.json())


def check_dns_anchor(card: AgentCard) -> tuple[bool | None, str]:
    """Resolve the agent's TXT record and confirm it pins this key.

    Returns (anchored, detail). `anchored` is None when the check was skipped -
    either the agent published no anchor, or ANS_DNS_VERIFY is off, which is the
    default while the demo runs on localhost with no real domain.
    """
    if not card.dns_anchor:
        return None, "Agent published no DNS anchor"
    if os.environ.get("ANS_DNS_VERIFY", "0") != "1":
        return None, "DNS verification disabled (set ANS_DNS_VERIFY=1 to enable)"

    try:
        import dns.resolver
    except ImportError:
        return None, "dnspython not installed; cannot verify DNS anchor"

    try:
        answers = dns.resolver.resolve(card.dns_anchor.record, "TXT")
    except Exception as exc:  # noqa: BLE001 - any DNS failure is a failed check
        return False, f"TXT lookup for {card.dns_anchor.record} failed: {exc}"

    expected = f"key=sha256:{card.key_fingerprint}"
    for record in answers:
        value = b"".join(record.strings).decode("utf-8", errors="replace")
        if expected in value:
            return True, f"Key pinned by TXT record at {card.dns_anchor.record}"

    return False, (
        f"TXT record at {card.dns_anchor.record} does not pin {card.key_fingerprint[:16]}..."
    )


def verify_envelope(envelope: SignedEnvelope, card: AgentCard) -> VerificationResult:
    """Authenticate a response against the card the agent published.

    Three things have to line up: the envelope names the same agent as the card,
    the key in the card hashes to the fingerprint the envelope claims, and the
    signature checks out over the payload.
    """
    if envelope.ans_name != card.ans_name:
        return VerificationResult(
            ans_name=envelope.ans_name,
            signature_valid=False,
            detail=f"Envelope claims {envelope.ans_name} but card says {card.ans_name}",
            trusted=False,
        )

    if fingerprint_of(card.public_key) != envelope.key_fingerprint:
        return VerificationResult(
            ans_name=envelope.ans_name,
            signature_valid=False,
            detail="Envelope fingerprint does not match the key in the agent card",
            trusted=False,
        )

    signature_valid = verify_signature(envelope.payload, envelope.signature, card.public_key)
    dns_anchored, dns_detail = check_dns_anchor(card)

    if not signature_valid:
        detail = "Signature did not verify against the published key"
    else:
        detail = f"Signature valid. {dns_detail}"

    # DNS anchoring not being attempted is not a trust failure; DNS anchoring
    # being attempted and *failing* is.
    trusted = signature_valid and dns_anchored is not False

    return VerificationResult(
        ans_name=envelope.ans_name,
        signature_valid=signature_valid,
        dns_anchored=dns_anchored,
        detail=detail,
        trusted=trusted,
    )
