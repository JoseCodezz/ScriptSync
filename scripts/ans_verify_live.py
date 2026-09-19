"""A third-party ANS verifier. Proves an agent's identity using only public DNS.

Runs the same round the ans-verify service runs, so the identity story is
demonstrable before src/server.ts is wired up:

    1. issue a fresh single-use challenge
    2. ask the agent to sign it
    3. read the agent's public key from public DNS - NOT from the agent
    4. verify the signature against that key

Step 3 is the point. This script never trusts anything the agent says about
itself; the key comes from the domain. An impostor can claim any name it likes
and still fails here.

    .venv/bin/python scripts/ans_verify_live.py
    .venv/bin/python scripts/ans_verify_live.py --agent simvastatin --port 9001
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brand_agent.ans import (  # noqa: E402
    parse_public_key_from_txt,
    signing_payload,
    verify_payload,
)

CHALLENGE_TTL_SECONDS = 120


def lookup_key_via_dns(
    domain: str, agent: str, nameserver: str | None = None
) -> tuple[str | None, str]:
    """Read the agent's public key from public DNS - registrar-agnostic.

    `nameserver` queries an authoritative server directly, bypassing recursive
    resolver caches. Useful right after publishing a record: a failed lookup is
    negatively cached for the zone's SOA minimum (1800s here), so public
    resolvers keep returning NoAnswer for up to 30 minutes after the record is
    actually live.
    """
    import dns.resolver

    hostname = f"_agentid.{agent}.{domain}"
    resolver = dns.resolver.Resolver()
    if nameserver:
        import socket

        resolver.nameservers = [socket.gethostbyname(nameserver)]
        resolver.cache = None
    try:
        answers = resolver.resolve(hostname, "TXT")
    except Exception as exc:  # noqa: BLE001
        return None, f"{hostname}: {type(exc).__name__}"
    for record in answers:
        value = b"".join(record.strings).decode("utf-8", errors="replace")
        key = parse_public_key_from_txt(value)
        if key:
            return key, hostname
    return None, f"{hostname}: no v=agentkey1 record"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a brand agent via public DNS")
    parser.add_argument("--domain", default="scriptsync.health")
    parser.add_argument("--agent", default=None, help="DNS label, e.g. simvastatin")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--nameserver", default=None,
        help="Query this nameserver directly, e.g. curitiba.ns.porkbun.com, to "
             "bypass recursive-resolver negative caching right after publishing.",
    )
    args = parser.parse_args()

    targets = (
        [(args.agent, args.endpoint or f"http://127.0.0.1:{args.port or 9001}")]
        if args.agent
        else [("simvastatin", "http://127.0.0.1:9001"),
              ("clarithromycin", "http://127.0.0.1:9002")]
    )

    failures = 0
    for agent, endpoint in targets:
        print(f"\n=== {agent} @ {args.domain} ===")

        # 1. Challenge. Single-use nonce + timestamp, exactly as ans-verify issues.
        challenge = secrets.token_urlsafe(32)
        issued_at = int(time.time() * 1000)
        print(f"  challenge issued   {challenge[:24]}…")

        # 2. Agent signs it.
        try:
            response = httpx.post(
                f"{endpoint}/ans/challenge",
                json={"domain": args.domain, "agent": agent,
                      "challenge": challenge, "issuedAt": issued_at},
                timeout=15,
            )
            response.raise_for_status()
            proof = response.json()
        except Exception as exc:  # noqa: BLE001
            print(f"  AGENT UNREACHABLE  {exc}")
            failures += 1
            continue
        print(f"  agent signed       {proof['signature'][:24]}…")

        # 3. Key from DNS, not from the agent.
        dns_key, where = lookup_key_via_dns(args.domain, agent, args.nameserver)
        if not dns_key:
            print(f"  DNS LOOKUP FAILED  {where}")
            print("  -> record missing, or negatively cached by your resolver.")
            print("     Retry with --nameserver curitiba.ns.porkbun.com to check")
            print("     the authoritative server directly.")
            failures += 1
            continue
        print(f"  key from DNS       {dns_key[:24]}… ({where})")

        if dns_key != proof["publicKey"]:
            print("  KEY MISMATCH       agent presented a key the domain does not publish")
            failures += 1
            continue

        # 4. Verify.
        payload = signing_payload(args.domain, agent, challenge, issued_at)
        if verify_payload(payload, proof["signature"], dns_key):
            print(f"  VERIFIED           {proof['agentName']}")
        else:
            print("  SIGNATURE INVALID  key does not match the signature")
            failures += 1

        # Negative control: the same signature must not verify for another round.
        if verify_payload(
            signing_payload(args.domain, agent, challenge, issued_at + 1),
            proof["signature"], dns_key,
        ):
            print("  REPLAY CHECK FAILED - signature verified for a different round")
            failures += 1
        else:
            print("  replay rejected    (altered issuedAt does not verify)")

    print(f"\n{len(targets) - failures}/{len(targets)} agents verified")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
