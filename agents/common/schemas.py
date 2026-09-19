"""Wire contracts shared by the drug agents and the HCP assistant.

Every model here is part of the public surface between agents. Changing a field
means bumping the version segment of the agent's ANS name.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Agent card - served at GET /.well-known/agent.json
# --------------------------------------------------------------------------


class DNSAnchor(BaseModel):
    """Where to look in DNS to prove this agent is who it claims to be.

    The agent publishes the record it *expects* to exist. A verifier resolves
    that record independently and checks the key fingerprint matches. Control of
    the DNS zone is the root of trust.
    """

    record: str = Field(description="FQDN of the TXT record, e.g. _ans.renavex.example.com")
    type: Literal["TXT"] = "TXT"
    expected_value: str = Field(description="Exact TXT value the verifier should find")


class AgentCard(BaseModel):
    """Self-description an agent publishes so others can find and trust it."""

    ans_name: str = Field(description="protocol://AgentID.Capability.Provider.vX.Y.Z")
    display_name: str
    provider: str
    capability: str
    role: str
    protocol: str = "a2a"
    version: str
    endpoint: str = Field(description="Base URL this agent answers on")
    skills: list[str] = Field(default_factory=list)
    public_key: str = Field(description="Base64 Ed25519 public key (raw 32 bytes)")
    key_fingerprint: str = Field(description="sha256 hex of the raw public key")
    dns_anchor: DNSAnchor | None = None
    data_scope: str = Field(description="What this agent is and is not allowed to speak to")
    synthetic_data: bool = Field(
        default=True,
        description="True when the agent is backed by demo data rather than a real label",
    )


# --------------------------------------------------------------------------
# Consultation request / response
# --------------------------------------------------------------------------


class PatientContext(BaseModel):
    """De-identified clinical context forwarded to a drug agent.

    Deliberately narrow: a drug agent gets what it needs to reason about its own
    product and nothing more. No name, no MRN, no free-text note dump.
    """

    age: int | None = None
    sex: str | None = None
    egfr: float | None = Field(default=None, description="mL/min/1.73m2")
    uacr: float | None = Field(default=None, description="mg/g")
    a1c: float | None = Field(default=None, description="percent")
    bmi: float | None = None
    diagnoses: list[str] = Field(default_factory=list)
    current_medications: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    relevant_history: list[str] = Field(default_factory=list)
    notes: str | None = None


class ConsultRequest(BaseModel):
    """What the HCP assistant sends to a drug agent."""

    question: str = Field(description="The specific clinical question being asked")
    patient: PatientContext | None = None
    requesting_agent: str = Field(description="ANS name of the caller")
    request_id: str


class Citation(BaseModel):
    """Pointer back into the monograph so a claim can be checked."""

    field: str = Field(description="Dotted path into the monograph, e.g. pivotal_trials[0].results")
    quote: str = Field(description="The supporting text as it appears in the source")


class ConsultAnswer(BaseModel):
    """The clinical payload a drug agent produces."""

    summary: str = Field(description="Direct answer to the question, 2-4 sentences")
    suitability: Literal["appropriate", "appropriate_with_caution", "not_appropriate", "insufficient_data"]
    rationale: str = Field(description="Why, tied to this patient's specifics")
    dosing_recommendation: str | None = Field(
        default=None, description="Concrete dose for this patient, or null if not appropriate"
    )
    patient_specific_flags: list[str] = Field(
        default_factory=list,
        description="Contraindications, warnings or interactions triggered by THIS patient",
    )
    monitoring: list[str] = Field(default_factory=list)
    supporting_evidence: list[Citation] = Field(default_factory=list)
    unknowns: list[str] = Field(
        default_factory=list,
        description="What this agent cannot answer from its own label - say so rather than guess",
    )
    confidence: Literal["high", "moderate", "low"]


class SignedEnvelope(BaseModel):
    """An answer plus the identity claims that make it checkable.

    `signature` covers the canonical JSON of `payload` only. The verifier
    re-canonicalizes and checks against the public key it resolved out of band.
    """

    ans_name: str
    request_id: str
    issued_at: str
    payload: dict[str, Any]
    signature: str = Field(description="Base64 Ed25519 signature over canonical JSON of payload")
    key_fingerprint: str


class VerificationResult(BaseModel):
    """What the assistant learned when it tried to authenticate a responder."""

    ans_name: str
    signature_valid: bool
    dns_anchored: bool | None = Field(
        default=None, description="None when DNS verification was not attempted"
    )
    detail: str
    trusted: bool
