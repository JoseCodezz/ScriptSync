"""The HCP assistant: the agent that sits with the physician.

It does three things, in order:

    intake      free-text from the clinician  ->  structured case + question
    consult     fan out that question to every drug agent, in parallel,
                verifying each responder's identity before trusting its answer
    synthesize  weigh the answers against each other and hand the physician
                one comparative brief

It deliberately does not know anything about the drugs. Everything clinical
comes from the drug agents, which is what makes the set extensible: register a
third drug agent and this file does not change.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from ..common.llm import MODEL, MAX_TOKENS, get_async_client, text_of
from ..common.registry import (
    ResolutionError,
    fetch_agent_card,
    known_agents,
    resolve,
    verify_envelope,
)
from ..common.schemas import (
    AgentCard,
    ConsultAnswer,
    ConsultRequest,
    PatientContext,
    SignedEnvelope,
    VerificationResult,
)

ASSISTANT_ANS_NAME = "a2a://hcp-assistant.clinical-orchestration.scriptsync.v1.0.0"

INTAKE_SYSTEM = """You prepare a clinical consultation for a physician. You will be given the \
physician's question in their own words, and whatever patient record material they attached.

Your job is to turn that into a structured case and one sharp question to put to a panel of drug \
information agents. Each agent knows exactly one drug and nothing else, so the question must be \
answerable by a single-product specialist - "is your drug right for this patient, at what dose, and \
what would stop you" rather than "which drug is better".

Rules:
- Extract only what is actually stated. Leave a field null rather than inferring it. A guessed eGFR \
is worse than a missing one because downstream agents will dose against it.
- Put anything clinically load-bearing that has no dedicated field into `relevant_history`.
- `omitted_but_needed` is where you flag what the physician did not give you that would change the \
answer. This goes back to them, so be specific: "no potassium" not "more labs".
- Never invent an identifier, a name, or a date. Do not copy patient identifiers into any field."""

SYNTHESIS_SYSTEM = """You are the physician's assistant agent. You put one question to several drug \
information agents, each of which speaks only for its own product and each of which was \
cryptographically verified before you accepted its answer. Now you write the brief the physician \
actually reads.

Write for a clinician who has ninety seconds. Lead with the answer.

What the brief must do:
- Open with a direct recommendation, and say plainly how confident you are and why.
- Make the real tradeoff explicit. These agents are advocates for their own products; your value is \
in reading across them. Where they emphasize different endpoints, say so.
- Hold every patient-specific flag any agent raised. A contraindication one agent surfaced about \
itself is the single most important thing on the page.
- Distinguish what is evidence-backed from what is extrapolated. When an agent said a patient falls \
outside its trial population, that belongs in the brief.
- Name what is still unknown and what the physician should go get.
- Note any agent whose identity could not be verified, and discount its claims accordingly.

What the brief must not do:
- Do not introduce clinical facts the agents did not provide. You have no independent drug \
knowledge in this role. If both agents were silent on something, it is an open question, not \
something for you to fill in.
- Do not flatten the disagreement into false balance. If one option is clearly better for this \
patient, say so.
- Do not hedge into uselessness. The physician decides; your job is to make the decision faster and \
better informed, not to refuse to have a view.

End with a short "What I could not answer" section. Markdown, no preamble, no restating the \
question back."""


class CaseIntake(BaseModel):
    """The structured case the assistant builds from the physician's input."""

    patient: PatientContext
    question_for_agents: str = Field(
        description="One question, answerable by a single-product specialist"
    )
    clinical_goal: str = Field(description="What the physician is actually trying to achieve")
    urgency: Literal["routine", "soon", "urgent"] = "routine"
    omitted_but_needed: list[str] = Field(
        default_factory=list, description="Specific missing data that would change the answer"
    )


@dataclass
class AgentResponse:
    """One drug agent's reply, with the trust verdict attached."""

    ans_name: str
    card: AgentCard | None = None
    answer: ConsultAnswer | None = None
    verification: VerificationResult | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.answer is not None and self.error is None


@dataclass
class ConsultationResult:
    intake: CaseIntake
    responses: list[AgentResponse] = field(default_factory=list)
    brief: str = ""

    @property
    def trusted_responses(self) -> list[AgentResponse]:
        return [r for r in self.responses if r.usable and r.verification and r.verification.trusted]


class HCPAssistant:
    """Entry point for a main program.

        assistant = HCPAssistant()
        result = await assistant.consult(hcp_prompt, patient_record_text=note)
        print(result.brief)
    """

    def __init__(
        self,
        agent_names: list[str] | None = None,
        timeout: float = 120.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.agent_names = agent_names or known_agents()
        self.timeout = timeout
        # Injectable so tests can mount the agents in-process, and so a
        # deployment can supply its own mTLS or proxy transport.
        self.http_transport = http_transport
        self.ans_name = ASSISTANT_ANS_NAME

    # -- step 1 -----------------------------------------------------------

    async def intake(self, hcp_prompt: str, patient_record_text: str | None = None) -> CaseIntake:
        client = get_async_client()
        content = f"PHYSICIAN'S QUESTION\n{hcp_prompt}"
        if patient_record_text:
            content += f"\n\nATTACHED PATIENT RECORD\n{patient_record_text}"

        response = await client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=INTAKE_SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_format=CaseIntake,
        )
        return response.parsed_output

    # -- step 2 -----------------------------------------------------------

    async def _ask_one(
        self, ans_name: str, intake: CaseIntake, request_id: str, client: httpx.AsyncClient
    ) -> AgentResponse:
        result = AgentResponse(ans_name=ans_name)
        try:
            endpoint = resolve(ans_name)
        except ResolutionError as exc:
            result.error = f"Could not resolve name: {exc}"
            return result

        try:
            card = await fetch_agent_card(endpoint, client)
            result.card = card
        except Exception as exc:  # noqa: BLE001
            result.error = f"Could not fetch agent card from {endpoint}: {exc}"
            return result

        request = ConsultRequest(
            question=intake.question_for_agents,
            patient=intake.patient,
            requesting_agent=self.ans_name,
            request_id=request_id,
        )

        try:
            response = await client.post(
                f"{endpoint}/ask", json=request.model_dump(mode="json"), timeout=self.timeout
            )
            response.raise_for_status()
            envelope = SignedEnvelope.model_validate(response.json())
        except Exception as exc:  # noqa: BLE001
            result.error = f"Consultation failed: {exc}"
            return result

        result.verification = verify_envelope(envelope, card)
        if not result.verification.signature_valid:
            result.error = f"Rejected unverified response: {result.verification.detail}"
            return result

        if envelope.request_id != request_id:
            result.error = "Response request_id did not match the request"
            return result

        try:
            result.answer = ConsultAnswer.model_validate(envelope.payload)
        except Exception as exc:  # noqa: BLE001
            result.error = f"Malformed answer payload: {exc}"

        return result

    async def consult_agents(self, intake: CaseIntake) -> list[AgentResponse]:
        """Ask every registered drug agent the same question, concurrently."""
        request_id = str(uuid.uuid4())
        async with httpx.AsyncClient(transport=self.http_transport) as client:
            return list(
                await asyncio.gather(
                    *(
                        self._ask_one(name, intake, request_id, client)
                        for name in self.agent_names
                    )
                )
            )

    # -- step 3 -----------------------------------------------------------

    def _render_responses(self, responses: list[AgentResponse]) -> str:
        blocks: list[str] = []
        for response in responses:
            if response.error:
                blocks.append(
                    f"### {response.ans_name}\nUNAVAILABLE - {response.error}\n"
                    "Treat this product as unassessed. Do not reason about it from your own knowledge."
                )
                continue

            assert response.answer is not None
            verification = response.verification
            trust_line = (
                f"Identity: {'VERIFIED' if verification and verification.trusted else 'UNVERIFIED'}"
                f" - {verification.detail if verification else 'no verification performed'}"
            )
            product = response.card.display_name if response.card else response.ans_name
            blocks.append(
                f"### {product}\n"
                f"ANS name: {response.ans_name}\n"
                f"{trust_line}\n"
                f"Synthetic demo data: {response.card.synthetic_data if response.card else 'unknown'}\n\n"
                f"{response.answer.model_dump_json(indent=2)}"
            )
        return "\n\n".join(blocks)

    async def synthesize(self, intake: CaseIntake, responses: list[AgentResponse]) -> str:
        client = get_async_client()
        content = (
            f"CLINICAL GOAL\n{intake.clinical_goal}\n\n"
            f"QUESTION PUT TO THE AGENTS\n{intake.question_for_agents}\n\n"
            f"URGENCY: {intake.urgency}\n\n"
            f"PATIENT\n{intake.patient.model_dump_json(indent=2)}\n\n"
            f"DATA THE PHYSICIAN DID NOT PROVIDE\n"
            + ("\n".join(f"- {item}" for item in intake.omitted_but_needed) or "- none flagged")
            + f"\n\nAGENT RESPONSES\n{self._render_responses(responses)}"
        )

        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYNTHESIS_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        return text_of(response)

    # -- the whole flow ---------------------------------------------------

    async def consult(
        self, hcp_prompt: str, patient_record_text: str | None = None
    ) -> ConsultationResult:
        intake = await self.intake(hcp_prompt, patient_record_text)
        responses = await self.consult_agents(intake)
        brief = await self.synthesize(intake, responses)
        return ConsultationResult(intake=intake, responses=responses, brief=brief)
