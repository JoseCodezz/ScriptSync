"""Integration test for everything except the model call itself.

Both drug agents run as real ASGI apps behind a real httpx client stack, with
only the Anthropic SDK stubbed out. Exercises agent cards, the wire schemas,
Ed25519 signing, envelope verification, tamper rejection, and the assistant's
fan-out plus synthesis path.

    python -m tests.test_pipeline
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from agents.common.schemas import ConsultAnswer, Citation, SignedEnvelope, AgentCard
from agents.common.registry import verify_envelope
from agents.drug_agent.service import build_app
from agents.hcp_assistant import HCPAssistant
from agents.hcp_assistant.assistant import CaseIntake

ROOT = Path(__file__).resolve().parents[1]

AGENTS = {
    "renavex.local": ROOT / "data" / "company_a.json",
    "glyvera.local": ROOT / "data" / "company_b.json",
}
NAME_TO_HOST = {
    "a2a://renavex.drug-information.corvus-therapeutics.v1.0.0": "http://renavex.local",
    "a2a://glyvera.drug-information.meridian-biopharma.v1.0.0": "http://glyvera.local",
}

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(f"{name}{(' - ' + detail) if detail else ''}")


# --- stub model -----------------------------------------------------------


def _stub_answer(brand: str) -> ConsultAnswer:
    return ConsultAnswer(
        summary=f"Stubbed answer about {brand}.",
        suitability="appropriate_with_caution",
        rationale="Stub rationale referencing eGFR 38.",
        dosing_recommendation="10 mg once daily",
        patient_specific_flags=["Recurrent UTI history"],
        monitoring=["Renal function at 4 weeks"],
        supporting_evidence=[Citation(field="dosing.renal_adjustment", quote="eGFR 25 to 44")],
        unknowns=["No data on concurrent knee arthroplasty timing"],
        confidence="moderate",
    )


class StubMessages:
    def __init__(self, brand_hint: dict) -> None:
        self.brand_hint = brand_hint
        self.parse_calls = 0
        self.create_calls = 0
        self.request_ids: list[str] = []

    def reset(self) -> None:
        self.parse_calls = 0
        self.create_calls = 0
        self.request_ids = []

    async def parse(self, **kwargs):
        self.parse_calls += 1
        output_format = kwargs["output_format"]
        if output_format is CaseIntake:
            parsed = CaseIntake(
                patient={"age": 64, "sex": "male", "egfr": 38.0, "a1c": 8.4, "uacr": 640.0,
                         "bmi": 34.2, "diagnoses": ["T2DM", "CKD 3b"],
                         "current_medications": ["metformin", "insulin glargine"],
                         "relevant_history": ["recurrent UTI"]},
                question_for_agents="Is your product appropriate here, at what dose?",
                clinical_goal="Add a second agent for glycemic and renal benefit",
                urgency="routine",
                omitted_but_needed=["No potassium trend"],
            )
        else:
            system = kwargs["system"]
            text = system[0]["text"] if isinstance(system, list) else system
            user_turn = kwargs["messages"][0]["content"]
            for line in user_turn.splitlines():
                if line.startswith("Request ID: "):
                    self.request_ids.append(line.removeprefix("Request ID: "))
            brand = "Renavex" if "Renavex" in text[:400] else "Glyvera"
            parsed = _stub_answer(brand)
        return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn")

    async def create(self, **kwargs):
        self.create_calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="..."),
                     SimpleNamespace(type="text", text="# Stub brief\n\nBoth agents responded.")],
            stop_reason="end_turn",
        )


STUB = StubMessages({})
STUB_CLIENT = SimpleNamespace(messages=STUB)


# --- routing transport ----------------------------------------------------


class HostRoutingTransport(httpx.AsyncBaseTransport):
    """Dispatch requests to the right in-process ASGI app by hostname."""

    def __init__(self, apps: dict[str, object]) -> None:
        self._transports = {
            host: httpx.ASGITransport(app=app) for host, app in apps.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._transports.get(request.url.host)
        if transport is None:
            raise httpx.ConnectError(f"no app for host {request.url.host}")
        return await transport.handle_async_request(request)


async def main() -> None:
    apps = {
        host: build_app(path, endpoint=f"http://{host}", dns_domain="scriptsync.example")
        for host, path in AGENTS.items()
    }
    transport = HostRoutingTransport(apps)

    # 1. Agent cards
    async with httpx.AsyncClient(transport=transport) as client:
        for host in AGENTS:
            resp = await client.get(f"http://{host}/.well-known/agent.json")
            check(f"card served ({host})", resp.status_code == 200, f"status {resp.status_code}")
            card = AgentCard.model_validate(resp.json())
            check(f"card has key ({host})", len(card.public_key) > 20)
            check(f"card has dns anchor ({host})", card.dns_anchor is not None)
            check(f"card flags synthetic ({host})", card.synthetic_data is True)

        health = await client.get("http://renavex.local/health")
        check("health endpoint", health.status_code == 200 and health.json()["status"] == "ok")

    # 2. Signed answer + verification + tamper rejection
    with patch("agents.drug_agent.service.get_async_client", return_value=STUB_CLIENT):
        async with httpx.AsyncClient(transport=transport) as client:
            body = {
                "question": "Appropriate for this patient?",
                "patient": {"age": 64, "egfr": 38.0},
                "requesting_agent": "a2a://test.harness.scriptsync.v1.0.0",
                "request_id": "req-test-1",
            }
            resp = await client.post("http://renavex.local/ask", json=body, timeout=30)
            check("ask returns 200", resp.status_code == 200, resp.text[:200])
            envelope = SignedEnvelope.model_validate(resp.json())
            card = AgentCard.model_validate(
                (await client.get("http://renavex.local/.well-known/agent.json")).json()
            )

            verdict = verify_envelope(envelope, card)
            check("signature verifies", verdict.signature_valid, verdict.detail)
            check("trusted without dns check", verdict.trusted, verdict.detail)
            check("dns check skipped by default", verdict.dns_anchored is None)

            tampered = envelope.model_copy(deep=True)
            tampered.payload["suitability"] = "appropriate"
            bad = verify_envelope(tampered, card)
            check("tampered payload rejected", not bad.signature_valid and not bad.trusted)

            wrong_card = card.model_copy(deep=True)
            other = AgentCard.model_validate(
                (await client.get("http://glyvera.local/.well-known/agent.json")).json()
            )
            wrong_card.public_key = other.public_key
            swapped = verify_envelope(envelope, wrong_card)
            check("key substitution rejected", not swapped.trusted)

    # 3. Full assistant flow
    STUB.reset()
    with patch("agents.drug_agent.service.get_async_client", return_value=STUB_CLIENT), \
         patch("agents.hcp_assistant.assistant.get_async_client", return_value=STUB_CLIENT), \
         patch("agents.hcp_assistant.assistant.resolve", side_effect=lambda n: NAME_TO_HOST[n]):
        assistant = HCPAssistant(agent_names=list(NAME_TO_HOST), http_transport=transport)
        result = await assistant.consult("Patient with T2D and CKD, what next?")

        check("two agents consulted", len(result.responses) == 2, str(len(result.responses)))
        errors = [r.error for r in result.responses if r.error]
        check("no consultation errors", not errors, str(errors))
        check("both trusted", len(result.trusted_responses) == 2)
        check("answers parsed", all(r.answer is not None for r in result.responses))
        check("distinct products answered",
              len({r.card.display_name for r in result.responses if r.card}) == 2)
        check("brief produced", result.brief.startswith("# Stub brief"))
        check("intake flagged gaps", result.intake.omitted_but_needed == ["No potassium trend"])
        check("one intake + one call per agent", STUB.parse_calls == 3,
              f"parse calls {STUB.parse_calls}")
        check("synthesis called once", STUB.create_calls == 1)
        check("fan-out shared one request_id",
              len(STUB.request_ids) == 2 and len(set(STUB.request_ids)) == 1,
              str(STUB.request_ids))

    # 4. Unresolvable name degrades, does not crash
    with patch("agents.hcp_assistant.assistant.get_async_client", return_value=STUB_CLIENT):
        assistant = HCPAssistant(agent_names=["a2a://ghost.drug-information.nobody.v1.0.0"],
                                 http_transport=transport)
        intake = await assistant.intake("test")
        responses = await assistant.consult_agents(intake)
        check("unknown agent degrades gracefully",
              len(responses) == 1 and responses[0].error is not None and not responses[0].usable)

    print("\n".join(f"  PASS  {p}" for p in PASSED))
    if FAILED:
        print("\n".join(f"  FAIL  {f}" for f in FAILED))
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    raise SystemExit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())
