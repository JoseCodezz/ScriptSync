"""Deterministic merge (F5, F6). No language model: overlaps and gaps come from
tags in the label data. Every statement is a verbatim label passage.

TAG CONTRACT (label authors must use these tag spellings):
  CYP3A, interaction, dosing, indication, monitoring, liver,
  renal, pregnancy, older-adults, pediatric, switching
  (switching = a passage that actually addresses changing from one drug to another.
   Most labels have none, so a switching question usually ends in 'Not covered'.)
"""

# Tags too generic to count as an overlap on their own.
IGNORED_TAGS = {"interaction", "dosing", "indication", "monitoring"}

# Coverage topics: tag -> keywords that mean the question is asking about it.
COVERAGE_TOPICS = {
    "renal": ["kidney", "renal", "creatinine", "dialysis"],
    "pregnancy": ["pregnan", "breastfeed", "lactation"],
    "older-adults": ["elderly", "older adult", "geriatric"],
    "pediatric": ["child", "pediatric", "infant"],
    "liver": ["liver", "hepatic"],
    "switching": ["switch", "transition", "convert", "conversion", "taper", "washout",
                  "change from", "changing from", "replace", "instead of"],
}

OVERLAP_NOTE = (
    "Possible overlap: both verified labels have passages on this topic. "
    "This is a cross-reference for clinician review, not a recommendation."
)


def find_overlaps(verified: list[dict]) -> list[dict]:
    by_tag: dict[str, dict[str, list[dict]]] = {}
    for src in verified:
        for ans in src.get("answers", []):
            for tag in ans.get("tags", []):
                if tag in IGNORED_TAGS:
                    continue
                by_tag.setdefault(tag, {}).setdefault(src["agent"], []).append(
                    {"source": src["brand"], "drug": src.get("drug"),
                     "section": ans.get("section"), "text": ans.get("text"),
                     "labelVersion": ans.get("labelVersion")}
                )
    overlaps = []
    for tag, per_source in by_tag.items():
        if len(per_source) >= 2:
            statements = [s for stmts in per_source.values() for s in stmts]
            overlaps.append({"tag": tag, "note": OVERLAP_NOTE, "statements": statements})
    return overlaps


def find_gaps(question: str, verified: list[dict], trusted: list[dict]) -> list[dict]:
    """`trusted` = every agent that passed verification (answered OR declined).
    An agent that verified but had no matching passage still counts: that is
    exactly when the honest answer is 'Not covered'."""
    if not trusted:
        return []
    q = question.lower()
    covered = {t for src in verified for a in src.get("answers", []) for t in a.get("tags", [])}
    gaps = []
    for tag, words in COVERAGE_TOPICS.items():
        if any(w in q for w in words) and tag not in covered:
            gaps.append({
                "topic": tag,
                "message": f"Not covered: no verified source has a label passage on '{tag}'. "
                           "ScriptSync does not guess.",
            })
    return gaps


def merge_results(question: str, results: list[dict]) -> dict:
    verified = [r for r in results if r["status"] == "verified"]
    trusted = [r for r in results if r["status"] in ("verified", "refused")]
    return {
        "question": question,
        "sources": verified,
        "refused": [r for r in results if r["status"] == "refused"],
        "blocked": [r for r in results if r["status"] == "blocked"],
        "unreachable": [r for r in results if r["status"] == "unreachable"],
        "skipped": [r for r in results if r["status"] == "skipped"],
        "overlaps": find_overlaps(verified),
        "gaps": find_gaps(question, verified, trusted),
        "disclaimer": "ScriptSync shows what approved labels say and where they may overlap, "
                      "for clinician review. It is not medical advice and never recommends a drug.",
    }
