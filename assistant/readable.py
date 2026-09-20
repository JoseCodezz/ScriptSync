"""Reading aids for verbatim label passages: what to emphasise and where to break lines.

DISPLAY ONLY. Nothing here writes, rewrites, shortens or summarises a clinical statement. Every output is a set of
character offsets into the passage's own `text`, which still reaches the doctor exactly as the label agent signed it.
The page uses the offsets to draw the same words with structure: the label's own headings on their own line, one
sentence per line, cross-references dimmed, the words of the question highlighted, and one "key point" (a sentence
copied out of the passage) picked by a fixed keyword rule. No model is involved, so the same passage and question
always give the same result, and a test checks that the offsets only ever describe the original text.

The result is `answer["view"]`:
    heads   [[start, end], ...]  the label's own headings ("6.1 Clinical Trials Experience", "Clinical Impact:", ...)
    refs    [[start, end], ...]  cross-references such as "[see CONTRAINDICATIONS ( 4 )]"
    hits    [[start, end], ...]  words from the doctor's question (never the drug's own name)
    key     [start, end] | None  the one sentence to read first; None when no sentence clearly stands out
    breaks  [offset, ...]        where a new line starts (sorted)
"""
from __future__ import annotations

import re

from assistant.merge import COVERAGE_TOPICS

# The label's own sub-headings that show up inline in the flat text.
LABEL_HEADS = re.compile(r"\b(?:Clinical Impact|Intervention|Examples):")
SUB_HEADS = re.compile(r"\b(?:Risk Summary|Clinical Considerations|Human Data|Animal Data|Absorption|Distribution|"
                       r"Metabolism|Elimination|Excretion|Pharmacodynamics)(?=\s+[A-Z0-9])")
# "[see CONTRAINDICATIONS ( 4 )]" and the bare "( 4 , 7.1 )" the label uses to point at another section.
REF = re.compile(r"\[see [^\]]{1,140}\]|\(\s*\d+(?:\.\d+)*\s*(?:[,.]\s*\d+(?:\.\d+)*\s*)+\)|\(\s*\d+\.\d+\s*\)")
SENTENCE_GAP = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
AMOUNT = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|mL|units?)\b", re.I)
DOSING_QUESTION = re.compile(r"\b(?:dos(?:e|es|ing|age)|how much|how often|max(?:imum)?|titrat\w*|starting)\b", re.I)
MAX_KEY = 320   # a "key point" longer than this is a paragraph, so a passage like that gets none
NOT_A_STOP = ("e.g.", "i.e.", "vs.", "approx.", "u.s.", "fig.", "no.", "etc.", "al.", "dr.")

# The first tier is the wording a clinician most needs to see: a prohibition or a required action.
STRONG_CUES = re.compile(
    r"\b(?:contraindicated|avoid(?:ed)?|do not|should not|must not|not recommended|discontinue|suspend|"
    r"do not exceed|not exceed|boxed warning|life-threatening|fatal)\b", re.I)
WEAK_CUES = re.compile(
    r"\b(?:risk of|limit(?:ed)?|reduce[ds]?|monitor(?:ing)?|dosage|dose|caution|adjust(?:ment)?|indicated|"
    r"recommended)\b", re.I)

STOP_WORDS = {
    "about", "above", "after", "again", "also", "among", "another", "because", "been", "before", "being", "between",
    "both", "could", "does", "doing", "each", "from", "give", "have", "having", "here", "into", "just", "like",
    "make", "many", "more", "most", "much", "need", "only", "other", "over", "same", "shall", "show", "should",
    "some", "such", "take", "taken", "taking", "tell", "than", "that", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "under", "until", "used", "very", "want", "were", "what", "when", "where",
    "which", "while", "will", "with", "within", "without", "would", "your", "safe", "okay", "know", "patient",
    "patients", "drug", "drugs", "medication", "medications", "medicine",
}


def _stem(word: str) -> str:
    """A prefix short enough to match the label's other forms of the word ("pregnant" also finds "pregnancy")."""
    return word if len(word) < 6 else word[: max(4, len(word) - 3)]


def question_terms(question: str, names: list[str], topics: list[str] | None = None) -> list[str]:
    """Stems worth highlighting: the question's own content words plus the words of any topic it asked about.
    The drug's own name is left out, because it is on almost every line and would highlight everything."""
    own = {part for name in names for part in re.findall(r"[a-z0-9]+", (name or "").lower())}
    words = [w for w in re.findall(r"[a-z][a-z0-9-]{3,}", question.lower()) if w not in STOP_WORDS and w not in own]
    for topic in topics or []:
        words.extend(COVERAGE_TOPICS.get(topic, []))
    stems: list[str] = []
    for word in words:
        stem = "dos" if re.fullmatch(r"dos(?:e|es|ing|age)", word) else _stem(word)   # dose, dosing, dosage
        if stem not in stems and not any(stem.startswith(s) for s in stems):
            stems.append(stem)
    return stems


def _ranges(pattern: re.Pattern, text: str) -> list[list[int]]:
    return [[m.start(), m.end()] for m in pattern.finditer(text)]


def _lead_heading(text: str, section: str, title: str) -> list[int] | None:
    """The heading the label puts in front of the passage: "4 CONTRAINDICATIONS", "6.1 Clinical Trials Experience",
    or a subtitle such as "Strong CYP3A4 inhibitors" before "Clinical Impact:"."""
    numbered = f"{section} {title}".strip().lower()
    if numbered and text.lower().startswith(numbered):
        return [0, len(numbered)]
    caps = re.match(r"\d+(?:\.\d+)*(?:\s+[A-Z][A-Z,&/()-]+)+", text)
    if caps:
        return [0, caps.end()]
    sub = re.match(r"([A-Z][^.:\[\]]{2,70}?)(?=\s+Clinical Impact:)|([A-Z][^.:\[\]]{2,70}:)", text)
    if sub:
        return [0, sub.end()]
    return None


def _sentence_starts(text: str, skip: list[list[int]]) -> list[int]:
    starts = []
    for gap in SENTENCE_GAP.finditer(text):
        before = text[: gap.start()].split()
        if before and before[-1].lower() in NOT_A_STOP:
            continue
        if any(s < gap.start() < e for s, e in skip):
            continue
        starts.append(gap.end())
    return starts


def _overlaps(start: int, end: int, ranges: list[list[int]]) -> bool:
    return any(start < e and s < end for s, e in ranges)


def _pick_key(text: str, lines: list[tuple[int, int]], heads: list[list[int]], refs: list[list[int]],
              terms_rx: re.Pattern | None, dosing: bool = False) -> list[int] | None:
    """The one sentence to read first: the best line by wording (a prohibition or an action), helped by the
    question's own words. Below a minimum score there is no key point, rather than a weak guess."""
    masked = list(text)
    for s, e in refs:
        masked[s:e] = " " * (e - s)   # "CONTRAINDICATIONS" inside "[see ...]" is a pointer, not a statement
    plain = "".join(masked)
    best, best_score = None, 0
    for start, end in lines:
        end = start + len(text[start:end].rstrip())
        if not 25 <= end - start <= MAX_KEY:
            continue
        # a heading line has nothing to say on its own
        if any(start >= s and end <= e for s, e in heads):
            continue
        line = plain[start:end]
        words = line.split()
        titled = sum(1 for w in words if w[:1].isupper() and w[1:].strip(".,:;()").islower())   # "Lovastatin", not "CYP3A4"
        if len(words) >= 8 and titled / len(words) > 0.25:
            continue   # a table flattened into one line ("Lomitapide Lovastatin Simvastatin Contraindicated ...")
        score =3 if STRONG_CUES.search(line) else 1 if WEAK_CUES.search(line) else 0
        if dosing and AMOUNT.search(line):
            score = max(score, 3)   # asked "how much": the sentence that states an amount is the answer
        if terms_rx:
            score += 2 * min(3, len(terms_rx.findall(line)))
        if score > best_score:
            best, best_score = [start, end], score
    return best if best_score >= 3 else None


def annotate(text: str, section: str = "", title: str = "", question: str = "", names: list[str] | None = None,
             topics: list[str] | None = None) -> dict:
    """The `view` for one passage. `text` is only ever read."""
    text = str(text or "")
    heads: list[list[int]] = []
    lead = _lead_heading(text, str(section or ""), str(title or ""))
    if lead:
        heads.append(lead)
    heads += [r for r in _ranges(LABEL_HEADS, text) if not lead or r[0] >= lead[1]]
    for r in _ranges(SUB_HEADS, text):
        before = text[: r[0]].rstrip()
        if (not before or (lead and r[0] <= lead[1] + 1) or before[-1] in ".]):") and not _overlaps(r[0], r[1], heads):
            heads.append(r)
    heads.sort()
    refs = [r for r in _ranges(REF, text) if not _overlaps(r[0], r[1], heads)]

    breaks = {0}
    for s, e in heads:
        breaks.add(s)
        if e < len(text):
            breaks.add(e + (len(text[e:]) - len(text[e:].lstrip())))
    breaks.update(_sentence_starts(text, refs))
    for _, end in refs:   # the label ends a list item with its pointer: "... cirrhosis [see WARNINGS ( 5.3 )] Hypersensitivity ..."
        rest = text[end:]
        gap = len(rest) - len(rest.lstrip())
        if gap and rest.lstrip()[:1].isupper():
            breaks.add(end + gap)
    cuts = sorted(b for b in breaks if 0 <= b < len(text))
    lines = list(zip(cuts, cuts[1:] + [len(text)]))

    dosing = bool(DOSING_QUESTION.search(question))
    stems = question_terms(question, names or [], topics)
    terms_rx = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in stems) + r")\w*", re.I) if stems else None
    hits = _ranges(terms_rx, text) if terms_rx else []
    if dosing:
        hits += _ranges(AMOUNT, text)
    hits = [r for r in hits if not _overlaps(r[0], r[1], heads) and not _overlaps(r[0], r[1], refs)]

    return {"heads": heads, "refs": refs, "hits": hits, "key": _pick_key(text, lines, heads, refs, terms_rx, dosing), "breaks": cuts}


def add_reading_aids(merged: dict, analysis: dict | None = None) -> None:
    """Attach `view` to every passage in an assembled /ask response. Only adds a field; `text` is untouched."""
    question = merged.get("question", "")
    topics = (analysis or {}).get("topics", [])
    asked = [d.get("drug") for d in (analysis or {}).get("drugsMentioned", [])]
    for source in merged.get("sources", []):
        names = [source.get("drug"), *asked]
        for answer in source.get("answers", []):
            answer["view"] = annotate(answer.get("text", ""), answer.get("section", ""), answer.get("title", ""),
                                      question, names, topics)
