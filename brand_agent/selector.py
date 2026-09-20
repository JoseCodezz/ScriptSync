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
    "dosing": ["dose", "dosing", "dosage", "how much", "how often", "mg", "titrate", "start"],
    "indication": ["indicated", "indication", "treat", "used for", "approved for", "what is it for"],
    "monitoring": ["monitor", "follow up", "follow-up", "labs", "lab test", "enzyme", "check", "watch"],
    "renal": ["kidney", "renal", "creatinine", "dialysis", "egfr", "crcl"],
    "liver": ["liver", "hepatic", "transaminase", "cirrhosis"],
    "older-adults": ["elderly", "older", "geriatric", "65", "75", "aged"],
    "pediatric": ["child", "pediatric", "paediatric", "infant", "adolescent", "kids"],
    "pregnancy": ["pregnan", "breastfeed", "lactation", "nursing", "fetal", "fetus", "trimester"],
    "adverse-reactions": ["side effect", "side-effect", "adverse", "reaction", "tolerab"],
    "warnings": ["warning", "precaution", "boxed", "black box"],
    "overdose": ["overdos", "too much"],
    "mechanism": ["mechanism", "how does", "how it works", "mode of action", "pharmacokinetic",
                  "half-life", "half life", "metaboli", "absorb", "excret", "clearance", "pharmacology"],
}

# Words that appear in many passage titles and say nothing about what the question is about.
GENERIC_TITLE_WORDS = {"important", "information", "recommended", "dosage", "patients", "clinical",
                       "experience", "modifications", "administration", "increase", "adults"}

# "tell me about X", "what is X": a bare question about the drug, which the label answers with what it is for.
GENERAL_QUESTION = (r"\btell me about\b", r"\bwhat is\b", r"\bwhat's\b", r"\boverview\b", r"\bexplain\b",
                    r"\bdescribe\b", r"\binformation (?:on|about)\b", r"\babout\b")


TOPIC_WORDS = {word for words in TAG_SYNONYMS.values() for word in words}
FILLER_WORDS = {"patients", "medication", "medications", "medicine", "medicines", "treatment", "therapy", "currently"}


def _has(text: str, phrase: str) -> bool:
    """True if `phrase` occurs at the start of a word ("interact" matches "interactions", not "counteract")."""
    return re.search(r"\b" + re.escape(phrase), text) is not None


def _long_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9-]{7,}", text))


def _other_names(text: str, drug_words: set[str]) -> set[str]:
    """Distinctive words in the question that could name another drug: long, and not a topic word or filler."""
    return {word for word in _long_words(text)
            if word not in drug_words and word not in GENERIC_TITLE_WORDS and word not in FILLER_WORDS
            and not any(word.startswith(topic) for topic in TOPIC_WORDS)}


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


def keyword_select(question: str, sections: list[dict[str, Any]], drug: str | None = None,
                   patient_context: str | None = None, limit: int = 4) -> Selection:
    """Deterministic fallback used when the model cannot be reached (or there is no API key).

    A passage scores 2 for each of its topic tags the question OR the patient context asks about
    (patient context - age, renal/liver function, current medications, never an identifier, see
    web/patient-context-template.txt - is matched the same way the question itself is: e.g. "Renal
    function: severe impairment" hits the "renal" tag exactly like a clinician typing "renal" would),
    and 1 for a distinctive word from its title. A named current medication scores 2 more when it
    appears in a section's own verbatim TEXT, not just its title. The drug's own name never counts:
    every title mentions the drug, so matching on it made any question that named the drug return the
    same passage. The best `limit` passages are returned, best first.
    """
    text = f"{question}\n{patient_context or ''}".lower()
    meds = [name.lower() for name in _current_medications(patient_context)]
    drug_words = set(re.findall(r"[a-z]+", (drug or "").lower()))
    named = bool(drug) and _has(text, drug.lower())
    # "Can <this drug> be taken with <other drug>?": the other drug's name is not a topic, but if this label's own
    # interaction passages mention it, those passages are exactly the answer. Only when this drug is named too,
    # so "tell me about <other drug>" is still refused here.
    others = _other_names(text, drug_words) if named else set()
    interaction_tags = {"interaction", "CYP3A"}
    # Which of this label's interaction passages actually name the other term. If none do, the label has nothing
    # on it (grapefruit juice, say) and we must not dress up generic interaction text as an answer.
    naming = {s["section"] for s in sections
              if interaction_tags & set(s["tags"]) and others & _long_words(s["text"].lower())}
    ranked = []
    for order, section in enumerate(sections):
        score = 0
        for tag in section["tags"]:
            if _has(text, tag.lower()) or any(_has(text, word) for word in TAG_SYNONYMS.get(tag, [])):
                score += 2
        if naming and interaction_tags & set(section["tags"]):
            score += 1                                    # a two-drug question is an interaction question
            if section["section"] in naming:
                score += 2                                # ...and a passage that names the other drug ranks first
        for word in set(re.findall(r"[a-z]{6,}", section["title"].lower())):
            if word not in drug_words and word not in GENERIC_TITLE_WORDS and _has(text, word):
                score += 1
        if any(_has(section["text"].lower(), med) for med in meds):
            score += 2                                    # a current medication named in this section's own text
        if score:
            ranked.append((-score, order, section["section"]))

    if ranked:
        ranked.sort()
        return Selection(sections=[number for _, _, number in ranked[:limit]])

    if named and (any(re.search(pattern, text) for pattern in GENERAL_QUESTION)
                  or re.fullmatch(r"\W*" + re.escape(drug.lower()) + r"\W*", text)):
        overview = [s["section"] for s in sections if "indication" in s["tags"]]
        if overview:
            return Selection(sections=overview[:1])
    return Selection(refuse=True, reason="No matching passage in this label.")


async def select(
    question: str,
    patient_context: str | None,
    drug: str,
    sections: list[dict[str, Any]],
) -> tuple[Selection, str]:
    """Return (selection, mode) where mode is 'model' or 'keyword-fallback'."""
    valid = {s["section"] for s in sections}

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # No key: do not attempt a model call that can only fail (it raised a TypeError on every question).
        return keyword_select(question, sections, drug, patient_context), "keyword-fallback (no ANTHROPIC_API_KEY)"

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
        return keyword_select(question, sections, drug, patient_context), f"keyword-fallback ({type(exc).__name__})"
