"""Read an agent's public GoDaddy ANS registration.

INFORMATIONAL ONLY. Nothing here changes a verification verdict: an agent's identity is still proven by the live
DNS-key + signed-challenge checks in assistant/verify.py. What this adds is the public, third-party record: that
GoDaddy's transparency log holds a registration for the agent's host, under what name, with a link anyone can open.

How it works. A registered host publishes a TXT record at `_ans-badge.<host>`:

    v=ans-badge1; version=v1.0.0; url=https://transparency.ans.godaddy.com/v1/agents/<id>

That URL is public JSON (no API key):

    {"merkleProof": {"leafIndex": 348783, "treeSize": 348854, ...},
     "payload": {"producer": {"event": {"ansId": ..., "ansName": "ans://v1.0.0.agent.<domain>",
                                        "eventType": "AGENT_REGISTERED",
                                        "agent": {"host": ..., "name": ..., "version": ...}}}}}

Safety: DNS is attacker-influenced, so the badge URL is only followed if it is on GoDaddy's transparency host, redirects
are not followed, the response is size-capped, and the entry's host must equal the host we asked about (a badge that
points at somebody else's entry is rejected).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

TRANSPARENCY_PREFIX = "https://transparency.ans.godaddy.com/"
MAX_BYTES = 200_000
OK_TTL_SECONDS = 600.0
FAIL_TTL_SECONDS = 60.0

_CACHE: dict[str, tuple[float, dict]] = {}


def parse_badge_txt(values: list[str]) -> str | None:
    """The transparency-log URL from `_ans-badge` TXT values, or None if absent or not on GoDaddy's host."""
    for value in values or []:
        if not value.strip().startswith("v=ans-badge1"):
            continue
        for part in value.split(";"):
            part = part.strip()
            if part.startswith("url="):
                url = part[len("url="):].strip()
                return url if url.startswith(TRANSPARENCY_PREFIX) else None
    return None


def parse_entry(doc: dict) -> dict:
    """The fields we show, pulled defensively out of a transparency-log entry."""
    event = ((doc.get("payload") or {}).get("producer") or {}).get("event") or {}
    agent = event.get("agent") or {}
    proof = doc.get("merkleProof") or {}
    return {
        "ansId": event.get("ansId"),
        "ansName": event.get("ansName"),
        "eventType": event.get("eventType"),
        "registeredName": agent.get("name"),
        "host": agent.get("host"),
        "version": agent.get("version"),
        "leafIndex": proof.get("leafIndex"),
        "treeSize": proof.get("treeSize"),
    }


def _default_resolver(hostname: str):
    from brand_agent.ans import resolve_txt   # DNS over port 53 with a DoH fallback
    return resolve_txt(hostname)


FETCH_ATTEMPTS = 3


def _default_fetch(url: str) -> tuple[int, bytes]:
    """Fetch the transparency entry, retrying briefly.

    The log intermittently answers 404 for an entry that exists - observed
    live on registered agents. A single attempt therefore caches a false
    "not registered" for a minute, which is how an ANS panel goes blank in
    the middle of a demo. Retry on anything that is not a 200.
    """
    import httpx

    last_status, last_body = 0, b""
    for attempt in range(FETCH_ATTEMPTS):
        try:
            response = httpx.get(url, timeout=8.0, follow_redirects=False)
            if response.status_code == 200:
                return response.status_code, response.content
            last_status, last_body = response.status_code, response.content
        except Exception:  # noqa: BLE001 - retry, then let the caller report it
            if attempt == FETCH_ATTEMPTS - 1:
                raise
        if attempt < FETCH_ATTEMPTS - 1:
            time.sleep(0.4 * (2 ** attempt))
    return last_status, last_body


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lookup(host: str, resolver: Callable | None = None, fetch: Callable | None = None) -> dict:
    """Return {ok: True, ...registration...} or {ok: False, host, error}. Cached; never raises."""
    host = host.strip().lower()
    cached = _CACHE.get(host)
    if cached and time.monotonic() - cached[0] < (OK_TTL_SECONDS if cached[1].get("ok") else FAIL_TTL_SECONDS):
        return cached[1]
    result = _lookup(host, resolver or _default_resolver, fetch or _default_fetch)
    _CACHE[host] = (time.monotonic(), result)
    return result


def _fail(host: str, error: str) -> dict:
    return {"ok": False, "host": host, "error": error}


def _lookup(host: str, resolver: Callable, fetch: Callable) -> dict:
    import json
    try:
        values, how = resolver(f"_ans-badge.{host}")
    except Exception as exc:  # noqa: BLE001 - informational; never break /agents
        return _fail(host, f"DNS lookup failed ({type(exc).__name__})")
    if values is None:
        return _fail(host, f"no ANS badge record found for this host, or DNS was unreachable ({how})")
    url = parse_badge_txt(values)
    if not url:
        return _fail(host, "no GoDaddy ANS badge record is published for this host")
    try:
        status, body = fetch(url)
    except Exception as exc:  # noqa: BLE001
        return _fail(host, f"could not reach the transparency log ({type(exc).__name__})")
    if status != 200:
        return _fail(host, f"the transparency log answered HTTP {status}")
    if len(body) > MAX_BYTES:
        return _fail(host, "the transparency-log entry was unexpectedly large")
    try:
        entry = parse_entry(json.loads(body))
    except ValueError:
        return _fail(host, "the transparency-log entry was not valid JSON")
    if (entry["host"] or "").lower() != host:
        return _fail(host, "the badge points at an entry for a different host")
    if not entry["ansName"]:
        return _fail(host, "the transparency-log entry has no agent name")
    return {"ok": True, "url": url, "checkedAt": _now_iso(), **entry}
