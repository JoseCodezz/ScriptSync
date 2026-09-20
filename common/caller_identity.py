"""Mutual authentication between the assistant and the label agents.

Identity already runs one way: the assistant proves who each agent is before it
accepts a word of medical content. This is the other half - the agent asking
"who is calling?" before it releases anything.

A caller signs a short assertion and sends it in headers:

    X-Agent-Identity   the caller's ANS name
    X-Agent-Timestamp  unix milliseconds
    X-Agent-Signature  base64 Ed25519 over the canonical payload below

    agent-caller-v1|<callerName>|<targetName>|<timestampMs>|<sha256 of the body>

The target name is included so a signature captured by one agent cannot be
replayed against another, and the body hash is included so the same signature
cannot be reused for a different question. The key is read from the caller's
own `_agentid` TXT record - the same mechanism and the same encoding the
agents already use, so there is nothing new to trust.
"""

from __future__ import annotations

import base64
import hashlib
import time

CALLER_PAYLOAD_VERSION = "agent-caller-v1"
MAX_AGE_MS = 120_000  # a signed call is good for two minutes


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def caller_payload(caller: str, target: str, timestamp_ms: int, digest: str) -> str:
    """The exact string both sides build. Any difference is a failed signature."""
    return f"{CALLER_PAYLOAD_VERSION}|{caller}|{target}|{timestamp_ms}|{digest}"


def sign_request(identity, target: str, body: bytes) -> dict[str, str]:
    """Headers proving this request came from `identity`, for this target and body."""
    timestamp_ms = int(time.time() * 1000)
    payload = caller_payload(identity.ans_name.full, target, timestamp_ms, body_digest(body))
    return {
        "X-Agent-Identity": identity.ans_name.full,
        "X-Agent-Timestamp": str(timestamp_ms),
        "X-Agent-Signature": identity.sign_payload(payload),
    }


def verify_request(headers, target: str, body: bytes, now_ms: int | None = None) -> dict:
    """Check a caller assertion. Returns {ok, caller, reason}. Never raises.

    Resolves the caller's public key from DNS rather than trusting anything in
    the request, so a caller cannot present its own key.
    """
    from brand_agent.ans import (
        ANSName, parse_public_key_from_txt, resolve_txt, verify_payload,
    )

    caller = (headers.get("x-agent-identity") or "").strip()
    signature = (headers.get("x-agent-signature") or "").strip()
    raw_timestamp = (headers.get("x-agent-timestamp") or "").strip()

    if not (caller and signature and raw_timestamp):
        return {"ok": False, "caller": caller or None, "reason": "unsigned request"}

    try:
        timestamp_ms = int(raw_timestamp)
    except ValueError:
        return {"ok": False, "caller": caller, "reason": "malformed timestamp"}

    age = abs((now_ms if now_ms is not None else int(time.time() * 1000)) - timestamp_ms)
    if age > MAX_AGE_MS:
        return {"ok": False, "caller": caller, "reason": f"stale call ({age // 1000}s old)"}

    try:
        parsed = ANSName.parse(caller)
    except ValueError as exc:
        return {"ok": False, "caller": caller, "reason": f"malformed caller name ({exc})"}

    values, how = resolve_txt(parsed.txt_record)
    if values is None:
        return {"ok": False, "caller": caller,
                "reason": f"could not resolve {parsed.txt_record} ({how})"}

    key = next((k for k in (parse_public_key_from_txt(v) for v in values) if k), None)
    if not key:
        return {"ok": False, "caller": caller,
                "reason": f"no key published at {parsed.txt_record}"}

    payload = caller_payload(caller, target, timestamp_ms, body_digest(body))
    if not verify_payload(payload, signature, key):
        return {"ok": False, "caller": caller,
                "reason": "signature does not match the key published for this caller"}

    return {"ok": True, "caller": caller, "reason": "signed by the key published in DNS"}
