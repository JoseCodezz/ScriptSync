"""A brand agent: answers only from one drug's approved FDA label.

Implements the contract in CLAUDE.md so the assistant and web UI work unchanged:

    GET  /identity        -> {agentName, ...ANS identity}
    POST /answer          -> signed answer, verbatim label passages only
    POST /ans/challenge   -> proof of key possession, for ANS verification

Replaces dev/mock_agent.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from common.signing import now_iso, sign_response

from .ans import AgentIdentity, ANSName, check_dns_anchor
from .selector import select


class AnswerRequest(BaseModel):
    question: str = ""
    # Additive and optional: the assistant may send de-identified context so the
    # agent can pick sections that matter for THIS patient. Older callers that
    # send only {question} keep working.
    patientContext: str | None = None


class ChallengeRequest(BaseModel):
    challenge: str


def build_app(label_path: Path, domain: str, version: str = "v1.0.0") -> FastAPI:
    label = json.loads(label_path.read_text())
    meta, agent_meta = label["_meta"], label["agent"]
    drug = agent_meta["drug"]
    sections = label["sections"]
    by_number = {s["section"]: s for s in sections}

    ans_name = ANSName.build(drug, domain, version)
    identity = AgentIdentity.load_or_create(ans_name)

    app = FastAPI(title=agent_meta["displayName"], version=version)

    @app.get("/identity")
    async def get_identity() -> dict:
        anchored, detail = check_dns_anchor(ans_name, identity.fingerprint)
        return {
            # The assistant reads only agentName; the rest is for ANS verification.
            "agentName": ans_name.full,
            "displayName": agent_meta["displayName"],
            "subtitle": agent_meta["subtitle"],
            "drug": drug,
            "brand": agent_meta["brand"],
            "labelVersion": label["labelVersion"],
            "labelSource": {
                "source": meta["source"],
                "splId": meta["spl_id"],
                "effectiveTime": meta["effective_time"],
            },
            "ans": {
                "publicKey": identity.public_key_b64,
                "keyAlgorithm": "ed25519",
                "keyEncoding": "base64-spki-der",
                "keyFingerprint": identity.fingerprint,
                "dnsRecord": ans_name.txt_record,
                "dnsTxtValue": identity.txt_value(),
                "dnsAnchored": anchored,
                "dnsDetail": detail,
            },
        }

    @app.post("/ans/challenge")
    async def ans_challenge(request: ChallengeRequest) -> dict:
        """Sign a verifier-supplied nonce, proving key possession right now."""
        return {
            "agentName": ans_name.full,
            "challenge": request.challenge,
            "signature": identity.sign_challenge(request.challenge),
            "publicKey": identity.public_key_b64,
        }

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "agent": ans_name.full, "drug": drug,
                "sections": len(sections)}

    @app.post("/answer")
    async def answer(request: AnswerRequest) -> dict:
        selection, mode = await select(
            request.question, request.patientContext, drug, sections
        )

        response: dict = {
            "agentName": ans_name.full,
            "brand": agent_meta["brand"],
            "refused": selection.refuse,
            "answers": [],
        }

        if selection.refuse:
            response["reason"] = selection.reason or (
                f"This agent only answers from the {drug} label."
            )
        else:
            # Text comes from the label file, never from the model.
            response["answers"] = [
                {
                    "section": by_number[number]["section"],
                    "title": by_number[number]["title"],
                    "labelVersion": label["labelVersion"],
                    "text": by_number[number]["text"],
                    "tags": by_number[number]["tags"],
                    "source": by_number[number]["source"],
                }
                for number in selection.sections
            ]

        response["selectionMode"] = mode
        response["timestamp"] = now_iso()
        response["signature"] = sign_response(response)
        return response

    return app
