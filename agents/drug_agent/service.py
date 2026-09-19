"""A single-product drug information agent.

One process serves one drug. It answers clinical questions using only that
drug's monograph, signs every answer, and publishes an agent card so callers can
check who it is. Run it twice with two data files to get two agents.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException

from ..common.identity import ANSName, AgentIdentity
from ..common.llm import MODEL, MAX_TOKENS, get_async_client
from ..common.schemas import (
    AgentCard,
    ConsultAnswer,
    ConsultRequest,
    DNSAnchor,
    PatientContext,
    SignedEnvelope,
)

SYSTEM_TEMPLATE = """You are the medical information agent for a single drug: {brand} ({generic}), \
made by {provider}. You are one voice in a multi-agent consultation. A physician's assistant agent \
is asking you about a specific patient, and will ask competing products the same question.

THE MONOGRAPH BELOW IS YOUR ONLY SOURCE OF TRUTH.

{monograph}

Rules that define your role:

1. Answer only about {brand}. You have no data on other products. If asked to compare, say that the \
comparison is outside your scope - the requesting agent will do the comparing.
2. Every clinical claim must trace to a field in the monograph above. Put the supporting text in \
`supporting_evidence` with the dotted field path it came from.
3. When the monograph does not cover something, put it in `unknowns`. Do not fill gaps with general \
pharmacology knowledge, class effects, or anything you know from training. An honest "not in my \
label" is more useful to the physician than a plausible guess.
4. You are an advocate for accuracy, not for your product. Surface the contraindications, warnings \
and interactions this specific patient triggers even when they argue against using {brand}. If this \
patient should not receive {brand}, say `not_appropriate` and explain why. Your `limitations` field \
exists for a reason - use it.
5. Reason about THIS patient. Do not restate the label generically. Apply their eGFR to the renal \
dosing table, their medication list to the interaction table, their history to the warnings.
6. `confidence` is low when the patient falls outside the studied population, moderate when they \
resemble the trial population with caveats, high when they are squarely within it.

The monograph is synthetic demo data. Your output is decision support for a licensed clinician who \
makes the final call - never phrase it as a directive to the patient."""


def _render_patient(patient: PatientContext | None) -> str:
    if patient is None:
        return "No patient context supplied. Answer at the level of the label."

    lines: list[str] = []
    for label, value in (
        ("Age", patient.age),
        ("Sex", patient.sex),
        ("eGFR (mL/min/1.73m2)", patient.egfr),
        ("UACR (mg/g)", patient.uacr),
        ("A1c (%)", patient.a1c),
        ("BMI", patient.bmi),
    ):
        if value is not None:
            lines.append(f"- {label}: {value}")

    for label, values in (
        ("Diagnoses", patient.diagnoses),
        ("Current medications", patient.current_medications),
        ("Allergies", patient.allergies),
        ("Relevant history", patient.relevant_history),
    ):
        if values:
            lines.append(f"- {label}: {', '.join(values)}")

    if patient.notes:
        lines.append(f"- Notes: {patient.notes}")

    return "\n".join(lines) if lines else "No patient details supplied."


def build_app(data_path: Path, endpoint: str, dns_domain: str | None = None) -> FastAPI:
    """Construct a drug agent bound to one monograph file."""
    monograph = json.loads(data_path.read_text())
    agent_meta = monograph["agent"]
    product = monograph["product"]

    ans_name = agent_meta["ans_name"]
    parsed_name = ANSName.parse(ans_name)
    identity = AgentIdentity.load_or_create(ans_name)

    # The monograph is large and identical on every request, so it goes at the
    # front of the system prompt behind a cache breakpoint. The question and
    # patient - the parts that change - go in the user turn, after it.
    system_prompt = SYSTEM_TEMPLATE.format(
        brand=product["brand_name"],
        generic=product["generic_name"],
        provider=product["manufacturer"],
        monograph=json.dumps(monograph, indent=2),
    )

    dns_anchor = None
    if dns_domain:
        record = f"_ans.{parsed_name.agent_id}.{dns_domain}"
        dns_anchor = DNSAnchor(record=record, expected_value=identity.dns_txt_value())

    card = AgentCard(
        ans_name=ans_name,
        display_name=agent_meta["display_name"],
        provider=agent_meta["provider"],
        capability=agent_meta["capability"],
        role=agent_meta["role"],
        version=parsed_name.version,
        endpoint=endpoint,
        skills=[
            "dosing-recommendation",
            "contraindication-screening",
            "drug-interaction-check",
            "trial-evidence-lookup",
        ],
        public_key=identity.public_key_b64,
        key_fingerprint=identity.fingerprint,
        dns_anchor=dns_anchor,
        data_scope=(
            f"{product['brand_name']} ({product['generic_name']}) only. "
            "No knowledge of competing products."
        ),
        synthetic_data=bool(monograph.get("_meta", {}).get("synthetic")),
    )

    app = FastAPI(
        title=agent_meta["display_name"],
        description=agent_meta["role"],
        version=parsed_name.version,
    )

    @app.get("/.well-known/agent.json", response_model=AgentCard)
    async def agent_card() -> AgentCard:
        return card

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "agent": ans_name, "drug": product["brand_name"]}

    @app.post("/ask", response_model=SignedEnvelope)
    async def ask(request: ConsultRequest) -> SignedEnvelope:
        client = get_async_client()

        user_turn = (
            f"Requesting agent: {request.requesting_agent}\n"
            f"Request ID: {request.request_id}\n\n"
            f"QUESTION\n{request.question}\n\n"
            f"PATIENT\n{_render_patient(request.patient)}"
        )

        try:
            response = await client.messages.parse(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_turn}],
                output_format=ConsultAnswer,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 502
            raise HTTPException(status_code=502, detail=f"Model call failed: {exc}") from exc

        if response.stop_reason == "refusal":
            raise HTTPException(status_code=422, detail="Model declined to answer this request")

        answer: ConsultAnswer = response.parsed_output
        payload = answer.model_dump(mode="json")

        return SignedEnvelope(
            ans_name=ans_name,
            request_id=request.request_id,
            issued_at=datetime.now(timezone.utc).isoformat(),
            payload=payload,
            signature=identity.sign(payload),
            key_fingerprint=identity.fingerprint,
        )

    return app
