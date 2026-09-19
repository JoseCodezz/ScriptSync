"""Checks for the brand agents. Runs without an API key.

    .venv/bin/python -m tests.test_brand_agent
    VERIFY_VERBATIM=1 .venv/bin/python -m tests.test_brand_agent   # also re-checks
                                        every quote against live openFDA
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import urllib.parse
import urllib.request
from pathlib import Path

import httpx

from brand_agent.ans import (
    ANSName, AgentIdentity, verify_challenge, check_dns_anchor,
    signing_payload, parse_public_key_from_txt,
)
from brand_agent.service import build_app
from common.signing import verify_signature, check_freshness, SIGNED_FIELDS

ROOT = Path(__file__).resolve().parents[1]
LABELS = {"simvastatin": ROOT / "labels" / "simvastatin.json",
          "clarithromycin": ROOT / "labels" / "clarithromycin.json"}
TAGS = {"CYP3A", "interaction", "dosing", "indication", "monitoring", "liver",
        "renal", "pregnancy", "older-adults", "pediatric"}

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(f"{name}{(' - ' + detail) if detail else ''}")


def test_labels() -> None:
    for drug, path in LABELS.items():
        label = json.loads(path.read_text())
        sections = label["sections"]
        check(f"{drug}: has passages", len(sections) >= 6, str(len(sections)))
        check(f"{drug}: marked verbatim", label["_meta"]["verbatim"] is True)
        check(f"{drug}: no manufacturer in agent name",
              "brand" not in label["agent"]["displayName"].lower().replace("brand agent", ""))

        bad_tags = {t for s in sections for t in s["tags"]} - TAGS
        check(f"{drug}: tags match merge.py vocabulary", not bad_tags, str(bad_tags))

        numbers = [s["section"] for s in sections]
        check(f"{drug}: section numbers unique", len(numbers) == len(set(numbers)), str(numbers))

        texts = [s["text"] for s in sections]
        check(f"{drug}: no duplicate passages", len(texts) == len(set(texts)))

        # The section number must actually appear at the start of its own text.
        misfiled = [s["section"] for s in sections
                    if not s["text"].lstrip().startswith(s["section"])
                    and not s["source"].get("truncated")]
        check(f"{drug}: passages start with their section number", not misfiled, str(misfiled))

        check(f"{drug}: every passage records its source offsets",
              all({"field", "char_start", "char_end"} <= set(s["source"]) for s in sections))


def test_verbatim_against_openfda() -> None:
    """Re-fetch each SPL and confirm every stored quote is really in it."""
    if os.environ.get("VERIFY_VERBATIM") != "1":
        PASSED.append("verbatim re-check SKIPPED (set VERIFY_VERBATIM=1)")
        return
    for drug, path in LABELS.items():
        label = json.loads(path.read_text())
        query = urllib.parse.quote(f'id:"{label["_meta"]["spl_id"]}"')
        url = f"https://api.fda.gov/drug/label.json?search={query}&limit=1"
        with urllib.request.urlopen(url, timeout=30) as response:
            record = json.load(response)["results"][0]
        for section in label["sections"]:
            field = section["source"]["field"]
            source_text = " ".join(" ".join(record[field]).split())
            check(f"{drug} §{section['section']} is verbatim in openFDA",
                  section["text"] in source_text)


def test_ans_identity() -> None:
    name = ANSName.build("simvastatin", "scriptsync.health")
    check("ANS name parses", name.provider == "simvastatin" and name.domain == "scriptsync.health")
    check("ANS TXT record matches dns.ts recordNameFor",
          name.txt_record == "_agentid.simvastatin.scriptsync.health", name.txt_record)
    check("DNS label is a single valid label", name.dns_label == "simvastatin")

    for bad in ["simvastatin.v1.0.0.x.com", "a2a://a.b.c.d.e", "a2a://a.b.c.v1.0.example"]:
        try:
            ANSName.parse(bad)
            check(f"rejects malformed name {bad!r}", False)
        except ValueError:
            check(f"rejects malformed name {bad!r}", True)

    identity = AgentIdentity.load_or_create(name)
    nonce, issued = secrets.token_urlsafe(16), 1758300000000
    args = ("scriptsync.health", "simvastatin", nonce, issued)
    signature = identity.sign_challenge(*args)

    # Byte-for-byte match with buildSigningPayload() in ans-verify.
    check("signing payload matches ans-verify format",
          signing_payload(*args)
          == f"agent-identity-v1|scriptsync.health|simvastatin|{nonce}|{issued}",
          signing_payload(*args))

    check("challenge signature verifies", verify_challenge(*args, signature, identity.public_key_b64))
    check("different nonce rejected",
          not verify_challenge("scriptsync.health", "simvastatin", "other", issued,
                               signature, identity.public_key_b64))
    check("altered issuedAt rejected",
          not verify_challenge("scriptsync.health", "simvastatin", nonce, issued + 1,
                               signature, identity.public_key_b64))
    check("swapped agent name rejected",
          not verify_challenge("scriptsync.health", "clarithromycin", nonce, issued,
                               signature, identity.public_key_b64))

    other = AgentIdentity.load_or_create(ANSName.build("clarithromycin", "scriptsync.health"))
    check("other agent's key rejected",
          not verify_challenge(*args, signature, other.public_key_b64))
    check("agents have distinct keys", identity.fingerprint != other.fingerprint)

    # TXT value must round-trip through the dns.ts parser.
    check("TXT value uses v=agentkey1 format",
          identity.txt_value().startswith("v=agentkey1; k="), identity.txt_value()[:24])
    check("TXT value parses back to the full public key",
          parse_public_key_from_txt(identity.txt_value()) == identity.public_key_b64)

    # SPKI DER is what ans-verify/src/crypto/agentKeys.ts emits and reads.
    check("public key is SPKI DER", identity.public_key_b64.startswith("MCowBQYDK2Vw"))

    anchored, _ = check_dns_anchor(name, identity.public_key_b64)
    check("dns check off by default returns unknown, never a pass", anchored is None)

    os.environ["ANS_DNS_VERIFY"] = "1"
    anchored, detail = check_dns_anchor(name, identity.public_key_b64)
    # Before the TXT records are published this is False (record absent); after,
    # True. Either is a real answer - the thing that must never happen is a pass
    # that was not actually checked.
    check("live dns check returns a real verdict", anchored in (True, False), detail)
    if anchored:
        PASSED.append(f"DNS ANCHORED LIVE - {detail}")
    wrong, wrong_detail = check_dns_anchor(name, other.public_key_b64)
    check("a key not in DNS never passes", wrong is not True, wrong_detail)
    os.environ.pop("ANS_DNS_VERIFY")


async def test_service() -> None:
    app = build_app(LABELS["simvastatin"], domain="scriptsync.health")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        identity = (await client.get("/identity")).json()
        check("identity exposes agentName", "agentName" in identity)
        check("identity exposes ANS key", "publicKey" in identity["ans"])
        check("identity exposes ans-verify agent + domain",
              identity["ans"]["agent"] == "simvastatin"
              and identity["ans"]["domain"] == "scriptsync.health")

        nonce, issued = secrets.token_urlsafe(16), 1758300000000
        body = {"domain": "scriptsync.health", "agent": "simvastatin",
                "challenge": nonce, "issuedAt": issued}
        proof = (await client.post("/ans/challenge", json=body)).json()
        check("live challenge-response over HTTP",
              verify_challenge("scriptsync.health", "simvastatin", nonce, issued,
                               proof["signature"], identity["ans"]["publicKey"]))

        impostor = await client.post("/ans/challenge", json={**body, "agent": "clarithromycin"})
        check("refuses to sign for another identity", impostor.status_code == 400,
              str(impostor.status_code))

        answer = (await client.post(
            "/answer",
            json={"question": "simvastatin with clarithromycin - interaction?"},
        )).json()
        check("answer has every signed field", all(f in answer for f in SIGNED_FIELDS),
              str([f for f in SIGNED_FIELDS if f not in answer]))
        check("signature verifies with common.signing", verify_signature(answer))
        check("answer is fresh", check_freshness(answer)[0])
        check("answered the interaction question", not answer["refused"])

        label_text = {s["text"] for s in json.loads(LABELS["simvastatin"].read_text())["sections"]}
        check("every returned passage is verbatim from the label file",
              all(a["text"] in label_text for a in answer["answers"]))

        tampered = dict(answer)
        tampered["answers"] = [{**tampered["answers"][0], "text": "Safe to co-prescribe."}]
        check("tampered answer fails signature", not verify_signature(tampered))

        off_label = (await client.post(
            "/answer", json={"question": "What is the shelf life of insulin glargine?"}
        )).json()
        check("refuses what the label does not cover", off_label["refused"],
              str(off_label.get("answers"))[:80])
        check("refusal is still signed", verify_signature(off_label))


def main() -> None:
    test_labels()
    test_verbatim_against_openfda()
    test_ans_identity()
    asyncio.run(test_service())

    print("\n".join(f"  PASS  {p}" for p in PASSED))
    if FAILED:
        print("\n".join(f"  FAIL  {f}" for f in FAILED))
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    raise SystemExit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
