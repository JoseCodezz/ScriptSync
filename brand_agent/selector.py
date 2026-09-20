"""Choose which label passages answer a question.

The model sees the label and returns SECTION NUMBERS ONLY. The caller then looks
up the verbatim text from the label file. That split is the whole point: the
agent reasons about relevance - including about a specific patient's history -
but cannot author a medical claim, because it never emits prose that reaches the
clinician.

If the model is unavailable, `select()` falls back to keyword matching so the
demo still runs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field

MODEL = os.environ.get("SCRIPTSYNC_MODEL", "claude-opus-5")

# Picking sections from a short list is not a hard reasoning problem. Measured
# on the demo question: default effort 6.2s, medium 3.4s with identical output,
# low 2.4s but noisier (it pulled in a loosely related section). Medium keeps
# the answer and halves the clinician's wait.
EFFORT = os.environ.get("SCRIPTSYNC_EFFORT", "medium")


def selection_mode() -> str:
    """Which path section selection will take, for startup logging and /health."""
    return f"claude ({MODEL})" if os.environ.get("ANTHROPIC_API_KEY") else "keyword-fallback (no ANTHROPIC_API_KEY)"

SYSTEM = """You route a clinician's question to passages of ONE drug label: {drug}.

You will be given the numbered sections of that label. Return the section numbers whose text \
actually answers the question. Nothing else.

Hard limits on your role:
- You do not write clinical content. You choose sections. The server returns the label's own words.
- Choose a section only if its text genuinely addresses the question. Do not pad the list with \
loosely related sections - a passage that does not answer the question is noise in front of a \
clinician.
- If no section addresses the question, set refuse=true with a short reason. "This label does not \
cover that" is a correct and useful answer. Never stretch a passage to cover a gap.
- If the question is solely about a different drug, set refuse=true. But if the question involves \
an interaction between {drug} and another drug, and this label has a passage on it, that passage \
belongs in your answer - you are reporting what YOUR label says about that interaction.
- When patient details are supplied (age, renal function, current medications, history), use them \
to pick sections: a patient on an interacting drug makes the interaction section relevant; an \
older patient makes a geriatric section relevant; impaired renal function makes a renal dosing \
section relevant.
- Never recommend a drug, a dose, or a course of treatment. You are selecting reference passages."""


class Selection(BaseModel):
    """What the model is allowed to decide."""

    sections: list[str] = Field(
        default_factory=list, description="Section numbers to return, exactly as given"
    )
    refuse: bool = Field(default=False)
    reason: str = Field(default="", description="Why nothing matched; empty when refuse is false")


def _render_sections(sections: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[{s['section']}] {s['title']}  (tags: {', '.join(s['tags'])})\n{s['text']}"
        for s in sections
    )


# Words a clinician actually types -> the tag they mean. Mirrors the topic
# vocabulary in assistant/merge.py so the fallback and the gap detector agree.
TAG_SYNONYMS = {
    "CYP3A": ["cyp3a", "cyp 3a", "interaction", "interact", "co-prescribe", "coadminist",
              "together", "combine", "concomitant", "same time", "at once"],
    "interaction": ["interaction", "interact", "co-prescribe", "coadminist", "concomitant",
                    "together", "combine", "same time", "at once"],
    "dosing": ["dose", "dosing", "dosage", "how much", "mg", "titrate", "start"],
    "indication": ["indicated", "indication", "treat", "used for", "approved for"],
    "monitoring": ["monitor", "check", "follow up", "watch", "lab", "enzyme"],
    "renal": ["kidney", "renal", "creatinine", "dialysis", "egfr", "crcl"],
    "liver": ["liver", "hepatic", "transaminase"],
    "older-adults": ["elderly", "older", "geriatric", "65", "75", "aged"],
    "pediatric": ["child", "pediatric", "infant", "adolescent"],
    "pregnancy": ["pregnan", "breastfeed", "lactation", "nursing"],
}


def _contains_word(text: str, word: str) -> bool:
    """Whole-word containment, not substring: plain `in` would let the tag
    "indication" match inside "contraindications" - a real false positive this
    caught (see keyword_select's docstring test)."""
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


_MED_LIST_LINE = re.compile(r"(?im)^\s*current medications\s*:\s*(.+)$")


def _current_medications(patient_context: str | None) -> list[str]:
    """Just the drug names out of a "Current medications: ..." line in patient
    context (see web/patient-context-template.txt), if present. Deliberately
    narrow - only the structured field a doctor was told to put drug names in,
    not any other line of free text."""
    if not patient_context:
        return []
    match = _MED_LIST_LINE.search(patient_context)
    if not match:
        return []
    return [name.strip() for name in re.split(r"[,;]", match.group(1)) if len(name.strip()) >= 4]


def keyword_select(question: str, sections: list[dict[str, Any]], patient_context: str | None = None) -> Selection:
    """Deterministic fallback used when the model cannot be reached. Patient
    context (age, renal/liver function, current medications - never an
    identifier, see web/patient-context-template.txt) is matched the same way
    the question itself is: e.g. "Renal function: severe impairment" hits the
    "renal" tag exactly like a clinician typing "renal" in the question would.

    A named current medication gets one thing more: it's checked against a
    section's own verbatim TEXT, not just its title. Matching arbitrary
    context wording against body text is exactly the false positive this
    function's title-only rule exists to avoid (see below) - but a specific
    drug name doesn't carry that risk the way generic wording does, which is
    the same reasoning that makes an exact name-field search better than a
    free-text one (common/fda_data.py's _build_precise_search). Only the
    "Current medications" field gets this - not "Relevant history" or any
    other free text - so the risk stays scoped to names, not prose.
    """
    text = f"{question}\n{patient_context or ''}".lower()
    meds = [name.lower() for name in _current_medications(patient_context)]

    def matches(section: dict[str, Any]) -> bool:
        for tag in section["tags"]:
            if _contains_word(text, tag.lower()):
                return True
            if any(_contains_word(text, word) for word in TAG_SYNONYMS.get(tag, [])):
                return True
        if any(_contains_word(text, word) for word in section["title"].lower().split() if len(word) > 5):
            return True
        return any(_contains_word(section["text"].lower(), med) for med in meds)

    hits = [s["section"] for s in sections if matches(s)]
    if not hits:
        return Selection(refuse=True, reason="No matching passage in this label.")
    return Selection(sections=hits)


async def select(
    question: str,
    patient_context: str | None,
    drug: str,
    sections: list[dict[str, Any]],
) -> tuple[Selection, str]:
    """Return (selection, mode) where mode is 'model' or 'keyword-fallback'."""
    valid = {s["section"] for s in sections}

    try:
        import anthropic

        client = anthropic.AsyncAnthropic()
        content = f"QUESTION\n{question}"
        if patient_context:
            content += f"\n\nPATIENT CONTEXT\n{patient_context}"
        content += f"\n\nSECTIONS OF THE {drug.upper()} LABEL\n{_render_sections(sections)}"

        response = await client.messages.parse(
            model=MODEL,
            max_tokens=4000,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM.format(drug=drug),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": content}],
            output_format=Selection,
            output_config={"effort": EFFORT},
        )
        if response.stop_reason == "refusal":
            return Selection(refuse=True, reason="Request declined."), "model"

        selection: Selection = response.parsed_output
        # Drop anything hallucinated: only sections that exist in this label survive.
        selection.sections = [s for s in selection.sections if s in valid]
        if not selection.sections and not selection.refuse:
            selection.refuse = True
            selection.reason = "No matching passage in this label."
        return selection, "model"

    except Exception as exc:  # noqa: BLE001 - never let the demo die on an API problem
        # Silently degrading to keyword matching would hide a bad key or a rate
        # limit behind a plausible-looking answer, so name the cause.
        logging.getLogger(__name__).warning(
            "Model selection failed (%s: %s); using keyword fallback",
            type(exc).__name__, exc,
        )
        return keyword_select(question, sections, patient_context), f"keyword-fallback ({type(exc).__name__})"
