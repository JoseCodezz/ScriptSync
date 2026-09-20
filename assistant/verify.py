"""Identity verification (F3).

is_agent_verified(name) is the ONLY place the app talks to ANS. The return
shape is fixed - {name, ok, mode, checks:[{id, pass, message}], warnings} -
so nothing else in the project changes when the backing check changes.

Two of the four checks are now LIVE against scriptsync.health:

  dns   - resolve _agentid.<agent>.<domain> and read the published Ed25519 key
  cert  - challenge the agent to sign a fresh nonce, verify the signature
          against the key that came from DNS (not from the agent)

The other two are still SIMULATED, and say so in their own message text, because
there is no public registration log and no revocation registry yet:

  log      - would be a transparency log of registered agent names
  current  - would be a revocation / version-currency lookup

Fail closed: if DNS or the agent cannot be reached, the check FAILS. It never
degrades to "verified" because something was unavailable.
"""

import json
import os
import secrets
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECK_IDS = ["dns", "cert", "log", "current"]
DEFAULT_MESSAGES = {
    "dns": ("Domain record found", "No domain record found for this agent"),
    "cert": ("Certificate valid, matches name", "Certificate missing, expired, or not matching the name"),
    "log": ("Listed in the public registration log", "Not found in the public registration log"),
    "current": ("Not revoked; this is the current version", "Identity revoked or replaced by a newer version"),
}
SIMULATED_SUFFIX = " (simulated)"

# Which of the four checks are real today. /health reports this so the UI never has to guess.
LIVE_CHECKS = ["dns", "cert"]
SIMULATED_CHECKS = ["log", "current"]


def describe_verification() -> dict:
    """What the identity check is made of right now. Static (no network), so /health stays fast.

    `dnssecBypass` is the caveat that matters: while the registrar has an orphaned DS record on
    the zone, the DNS check only works with ANS_ALLOW_UNVALIDATED_DNS=1, which skips DNSSEC.
    """
    return {
        "mode": "live-dns",
        "live": LIVE_CHECKS,
        "simulated": SIMULATED_CHECKS,
        "dnssecBypass": os.environ.get("ANS_ALLOW_UNVALIDATED_DNS") == "1",
    }

# Live checks hit the network. Cache briefly so a page of results does not
# re-resolve DNS and re-challenge every agent on every request.
# Keyed by (name, endpoint), not name alone: two config entries can share an
# ANS name while pointing at different hosts - the replay attacker does exactly
# that - and they must not inherit each other's result.
_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
CACHE_TTL_SECONDS = 120.0    # a passed check is trusted this long (each answer is still signature-checked)
FAILURE_TTL_SECONDS = 15.0   # a failed one is retried soon, so a slow start cannot keep an agent blocked


def _load_agents():
    from common.agents_config import load_agents   # same endpoint overrides as the assistant
    return load_agents()


def _live_dns(ans_name: str) -> tuple[bool, str, str | None]:
    """Resolve the agent's key from DNS. Returns (passed, message, public_key)."""
    try:
        from brand_agent.ans import ANSName, parse_public_key_from_txt, resolve_txt
    except ImportError:
        return False, "Verification unavailable: ANS client not installed", None

    try:
        parsed = ANSName.parse(ans_name)
    except ValueError as exc:
        return False, f"Malformed agent name: {exc}", None

    values, how = resolve_txt(parsed.txt_record)
    if values is None:
        return False, f"Domain lookup failed for {parsed.txt_record}: {how}", None

    for value in values:
        key = parse_public_key_from_txt(value)
        if key:
            note = " [DNSSEC check bypassed]" if "UNVALIDATED" in how else ""
            return True, f"Public key published at {parsed.txt_record}{note}", key
    return False, f"No agent key published at {parsed.txt_record}", None


def _live_cert(agent: dict, public_key: str | None) -> tuple[bool, str]:
    """Challenge the agent to prove it holds the private key, right now."""
    if not public_key:
        return False, "No published key to check a signature against"

    try:
        import httpx

        from brand_agent.ans import ANSName, verify_challenge
    except ImportError:
        return False, "Verification unavailable: ANS client not installed"

    parsed = ANSName.parse(agent["ansName"])
    challenge, issued_at = secrets.token_urlsafe(32), int(time.time() * 1000)

    try:
        response = httpx.post(
            agent["endpoint"].rstrip("/") + "/ans/challenge",
            json={"domain": parsed.domain, "agent": parsed.dns_label,
                  "challenge": challenge, "issuedAt": issued_at},
            timeout=5.0,
        )
        response.raise_for_status()
        proof = response.json()
    except Exception as exc:  # noqa: BLE001 - unreachable or refusing is a failure
        return False, f"Agent could not prove key possession ({type(exc).__name__})"

    if proof.get("publicKey") != public_key:
        return False, "Agent presented a key the domain does not publish"

    if not verify_challenge(parsed.domain, parsed.dns_label, challenge, issued_at,
                            proof.get("signature", ""), public_key):
        return False, "Signature did not verify against the key published in DNS"

    return True, "Signed a fresh challenge with the key published in DNS"


def is_agent_verified(name: str, endpoint: str | None = None) -> dict:
    """Return {name, ok, mode, checks:[{id, pass, message}], warnings:[...]}.

    `endpoint` disambiguates entries that share an ANS name - which is exactly
    what an impostor does. Without it, a lookup by name alone can match the
    legitimate agent and challenge the wrong host, reporting a signature that
    the agent being checked never produced.
    """
    agents = _load_agents()
    agent = next(
        (a for a in agents
         if a["ansName"] == name and (endpoint is None or a["endpoint"] == endpoint)),
        None,
    )

    cache_key = (name, agent["endpoint"] if agent else "")
    cached = _CACHE.get(cache_key)
    if cached:
        ttl = CACHE_TTL_SECONDS if cached[1]["ok"] else FAILURE_TTL_SECONDS
        if time.monotonic() - cached[0] < ttl:
            return cached[1]

    if agent is None:
        # Unknown name: nothing registered anywhere.
        checks = [{"id": cid, "pass": False, "message": DEFAULT_MESSAGES[cid][1]}
                  for cid in CHECK_IDS]
        return {"name": name, "ok": False, "mode": "live", "checks": checks, "warnings": []}

    stub = agent.get("identity_stub", {})
    checks, live_used = [], False
    # Set by the dns check and consumed by the cert check, which verifies the
    # signature against the key the DOMAIN published - never one the agent sent.
    published_key: str | None = None

    for cid in CHECK_IDS:
        # A scripted attack scenario overrides the live result, and is labelled.
        if cid in stub:
            passed = bool(stub[cid].get("pass", True))
            ok_msg, bad_msg = DEFAULT_MESSAGES[cid]
            message = stub[cid].get("message", ok_msg if passed else bad_msg)
            checks.append({"id": cid, "pass": passed,
                           "message": message + SIMULATED_SUFFIX})
            continue

        if cid == "dns":
            passed, message, published_key = _live_dns(agent["ansName"])
            live_used = True
        elif cid == "cert":
            passed, message = _live_cert(agent, published_key)
            live_used = True
        else:
            # No public log or revocation registry exists yet - say so plainly
            # rather than presenting a stub as a real check.
            passed = True
            message = DEFAULT_MESSAGES[cid][0] + SIMULATED_SUFFIX

        checks.append({"id": cid, "pass": passed, "message": message})

    result = {
        "name": name,
        "ok": all(c["pass"] for c in checks),
        "mode": "live-dns" if live_used else "simulated",
        "checks": checks,
        "warnings": agent.get("warnings", []),
    }
    _CACHE[cache_key] = (time.monotonic(), result)
    return result


