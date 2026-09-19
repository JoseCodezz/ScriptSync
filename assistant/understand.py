"""Question understanding and input guards. Deterministic: no language model.

Everything here works from the question text and config/agents.json only:
  - find_phi():      refuse questions that contain patient identifiers (hard rule: no PHI)
  - analyze():       which known drugs are named, which topics, switching, advice-seeking
  - drug_gaps():     'Not covered' entries for drugs we have no verified source for
  - build_notices(): plain-English notices shown above the answers

Drug names come from each brand agent's `drug` (plus optional `aliases`) in
config/agents.json, so adding an agent teaches the assistant a new drug with no
code change. Nothing here ever recommends a drug, dose, or treatment.
"""
import re

from assistant.merge import COVERAGE_TOPICS

# ---------- patient identifiers (hard rule: no PHI anywhere) ----------
PHI_PATTERNS = {
    "a Social Security number": r"\b\d{3}-\d{2}-\d{4}\b",
    "a phone number": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b",
    "an email address": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "a full date (possible date of birth)": r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b",
    "a date of birth": r"\bdob\b|\bdate of birth\b|\bborn on\b",
    "a medical record number": r"\bmrn\b|\bmedical record (?:number|no\b)|\bpatient id\b",
    "a patient name": r"\bpatient(?:'s)? name\b|\bpatient named\b|\bnamed (?:mr|mrs|ms|dr)\b",
    "a street address": r"\b\d{1,5}\s+(?:[a-z]+\s+){1,2}(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|blvd|boulevard)\b",
}


def find_phi(text: str) -> list[str]:
    """Descriptions of likely patient identifiers in `text` (never the text itself)."""
    return [label for label, pat in PHI_PATTERNS.items() if re.search(pat, text, re.IGNORECASE)]


# ---------- advice-seeking wording ----------
ADVICE_PATTERNS = [
    r"\bshould (?:i|we)\b",
    r"\brecommend",
    r"\bsuggest",
    r"\bwhat would you\b",
    r"\b(?:which|what)\b.{0,40}\b(?:better|best|safer|safest|preferred)\b",
    r"\bbest (?:drug|option|choice|treatment|medication|alternative)\b",
    r"\bis it (?:safe|ok|okay|fine)\b",
    r"\b(?:safe|ok|okay) to (?:co-?prescribe|prescribe|give|start|switch|combine|use|take)\b",
    r"\bcan i (?:give|prescribe|switch|start|combine|use)\b",
]

ADVICE_NOTICE = (
    "ScriptSync does not recommend a drug, dose, or treatment. Below is what the verified "
    "labels say, as cross-references for clinician review."
)

# ---------- drugs named in a switching question that we have no agent for ----------
# Only extracted from explicit switching phrasing, to keep false positives rare.
_NAME = r"([a-z][a-z0-9-]{3,})"
# "§" stands in for a known drug name that was masked out (see analyze()).
_SWITCH_PATTERNS = [
    rf"\bfrom\s+(?:{_NAME}|§)\s+(?:to|onto)\s+{_NAME}",
    rf"\b(?:switch\w*|chang\w+|transition\w*|convert\w*|mov\w+)\s+(?:[\w§]+\s+){{0,3}}(?:to|onto)\s+{_NAME}",
    rf"\b(?:instead of|replac\w+)\s+{_NAME}",
]
_NOT_DRUGS = {
    "lower", "higher", "another", "other", "different", "alternative", "alternate", "generic",
    "brand", "same", "this", "that", "them", "their", "know", "drug", "drugs", "medication",
    "medications", "treatment", "therapy", "dose", "doses", "dosing", "week", "weeks", "month",
    "months", "twice", "once", "daily", "safely", "something", "anything", "what", "when",
    "patient", "patients", "class", "option", "options", "regimen", "formulation",
}


def _drug_names(agent: dict) -> list[str]:
    return [n for n in [agent.get("drug"), *agent.get("aliases", [])] if n]


def _word_regex(name: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(name.lower())}\b")


def analyze(question: str, agents: list[dict]) -> dict:
    """Deterministic read of the question. `agents` is the parsed config/agents.json list."""
    q = question.lower()
    drugs_mentioned, masked = [], q
    for agent in agents:
        if agent.get("role") == "attacker":
            continue
        hit = False
        for name in _drug_names(agent):
            rx = _word_regex(name)
            if rx.search(masked):
                hit = True
                masked = rx.sub("§", masked)  # so a known name is not re-read as an unknown drug
        if hit:
            drugs_mentioned.append({"drug": agent.get("drug"), "agent": agent["id"], "brand": agent["brand"]})

    topics = [tag for tag, words in COVERAGE_TOPICS.items() if any(w in q for w in words)]
    switching = "switching" in topics

    without_agent: list[str] = []
    if switching:
        for pat in _SWITCH_PATTERNS:
            for match in re.finditer(pat, masked):
                for word in match.groups():
                    if word and word not in _NOT_DRUGS and word not in without_agent:
                        without_agent.append(word)

    return {
        "drugsMentioned": drugs_mentioned,
        "drugsWithoutAgent": without_agent,
        "topics": topics,
        "switching": switching,
        "adviceSeeking": any(re.search(p, q) for p in ADVICE_PATTERNS),
    }


def drug_gaps(analysis: dict) -> list[dict]:
    """Same shape as merge.find_gaps entries, so the UI shows them as 'Not covered'."""
    return [{"topic": f"drug: {name}",
             "message": f"Not covered: no verified source is available for '{name}'. "
                        "ScriptSync does not guess."}
            for name in analysis["drugsWithoutAgent"]]


def build_notices(analysis: dict) -> list[dict]:
    notices = []
    if analysis["adviceSeeking"]:
        notices.append({"type": "advice", "message": ADVICE_NOTICE})
    return notices
