"""Build a brand agent's label file from the public FDA label (openFDA / DailyMed).

Passages are VERBATIM contiguous slices of the source SPL, auto-discovered from
every numbered section in the fields below - not hand-picked. Section numbers
and titles are read out of the label text itself, so a passage can never be
filed under a section it did not come from. Each passage records the openFDA
field and character offsets it was cut from, so anyone can re-fetch the SPL and
check the quote. Topic tags are inferred from the same word list a clinician's
question is matched against (brand_agent/selector.py's TAG_SYNONYMS), so
assistant/merge.py's overlap/gap detection has something to key off without a
person tagging every passage by hand.

    python scripts/build_label.py                  # both drugs
    python scripts/build_label.py simvastatin
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # allow running as `python scripts/build_label.py`

from brand_agent.selector import TAG_SYNONYMS  # noqa: E402
from common.fda_data import fetch_url_text  # noqa: E402

OPENFDA = "https://api.fda.gov/drug/label.json"
MAX_CHARS = 1500  # keeps a quote readable in the UI; always cut at a sentence end

# A handful of known sections have a hand-picked anchor point (see KNOWN below)
# that skips a section's preamble straight to the relevant part, so MAX_CHARS
# reaches further into it. A section with no anchor has no such shortcut, so a
# long one (some run 5000+ chars) can burn the whole budget on preamble before
# ever reaching the actual content - give those more room instead.
AUTO_MAX_CHARS = 3000

# How long a built label is trusted before an agent re-pulls it from openFDA.
# The FDA label itself changes rarely; this just bounds how stale "current"
# can get without anyone re-running this script.
DEFAULT_MAX_AGE_HOURS = 24.0

# The openFDA fields scanned for every numbered section they contain. These are
# the standard SPL fields a clinical question is likely to be about; KNOWN below
# seeds a few of them with a hand-verified title/anchor from before this was
# automatic, but every section in these fields is discovered and served, not
# just the ones listed there.
AUTO_FIELDS = (
    "contraindications", "drug_interactions", "dosage_and_administration",
    "indications_and_usage", "geriatric_use",
)

# A section header inside SPL text: "4.5 Lomitapide, Lovastatin, and Simvastatin"
HEADER = re.compile(
    r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s+([A-Z][A-Za-z0-9,\-/()' ]{3,70}?)(?=\s+[A-Z(]|\s*$)"
)

# Which SPL identifies each drug, plus a few sections worth seeding with a
# hand-verified title and, where a section is unusually long, an anchor point
# to skip its preamble (see AUTO_MAX_CHARS). Every OTHER numbered section in
# AUTO_FIELDS is still discovered and served - this only improves the display
# for sections someone already happened to read closely, it doesn't gate them.
KNOWN = {
    "simvastatin": {
        "spl_id": "4bbbb5c9-6c29-bc3b-e063-6294a90a6cce",
        "display": "Simvastatin label agent",
        "sections": {
            ("contraindications", "4"): {"title": "Contraindications"},
            ("drug_interactions", "7.1"): {
                "title": "Drug Interactions that Increase the Risk of Myopathy and Rhabdomyolysis with Simvastatin",
                "anchor": "Strong CYP3A4 inhibitors",
            },
            ("dosage_and_administration", "2.1"): {"title": "Important Dosage and Administration Information"},
            ("dosage_and_administration", "2.2"): {"title": "Recommended Dosage in Adult Patients"},
            ("dosage_and_administration", "2.5"): {"title": "Dosage Modifications Due to Drug Interactions"},
            ("indications_and_usage", "1"): {"title": "Indications and Usage"},
            ("geriatric_use", "8.5"): {"title": "Geriatric Use"},
        },
    },
    "clarithromycin": {
        "spl_id": "41392bb7-d916-446c-e063-6294a90a2c5c",
        "display": "Clarithromycin label agent",
        "sections": {
            ("contraindications", "4.5"): {"title": "Lomitapide, Lovastatin, and Simvastatin"},
            ("contraindications", "4.4"): {"title": "Colchicine"},
            ("drug_interactions", "7"): {
                "title": "Drug Interactions - lipid-lowering agents",
                "anchor": "Lipid-lowering agents",
            },
            ("dosage_and_administration", "2.2"): {"title": "Adult Dosage"},
            ("dosage_and_administration", "2.6"): {"title": "Dosage Adjustment in Patients with Renal Impairment"},
            ("dosage_and_administration", "2.7"): {"title": "Dosage Adjustment Due to Drug Interactions"},
            ("indications_and_usage", "1.3"): {"title": "Community-Acquired Pneumonia"},
            ("geriatric_use", "8.5"): {"title": "Geriatric Use"},
        },
    },
}

# A section's field says something about its topic regardless of wording
# ("dosage_and_administration" is about dosing even if the word "dose" never
# appears in a particular subsection's text) - these seed _infer_tags on top
# of the word-based TAG_SYNONYMS match.
_FIELD_TAG_HINTS = {
    "dosage_and_administration": ("dosing",),
    "indications_and_usage": ("indication",),
    "geriatric_use": ("older-adults",),
    "drug_interactions": ("interaction",),
}


def fetch_spl(spl_id: str) -> dict:
    query = urllib.parse.quote(f'id:"{spl_id}"')
    raw = fetch_url_text(f"{OPENFDA}?search={query}&limit=1")
    if raw.startswith("Try Again, "):
        raise RuntimeError(raw)
    return json.loads(raw)["results"][0]


# A real section header can follow almost any punctuation depending on how
# the SPL's own preceding sentence happens to end - a period, a closing
# paren, even a bare "%" - so there's no reliable "what comes before" rule.
# What's reliably wrong is a number that's actually a QUANTITY sitting in
# running prose or a flattened table: "...Aged 10 Years and Older...", or a
# duration column "...mg 7-14 Acute sinusitis..." - both otherwise match
# HEADER's "number, capitalized word" shape just as well as a real header.
_UNIT_WORDS = {
    "years", "year", "months", "month", "weeks", "week", "days", "day",
    "hours", "hour", "minutes", "minute", "mg", "kg", "mcg", "ml", "percent",
}


def _looks_like_header(chunk: str, start: int, title: str) -> bool:
    # Glued directly to a preceding hyphen/dash: the second half of a range
    # ("7-14"), not a section number.
    if start > 0 and chunk[start - 1] in "-–—":
        return False
    # Immediately followed by a unit word: a quantity ("10 Years...",
    # "40 mg"), not a header, whatever else follows it.
    first_word = title.split()[0].lower() if title.split() else ""
    return first_word not in _UNIT_WORDS


def find_all_sections(chunk: str) -> list[tuple[str, int, int, str]]:
    """Every numbered section in one chunk: [(number, start, end, title), ...]
    from the label's own headers."""
    headers = [m for m in HEADER.finditer(chunk) if _looks_like_header(chunk, m.start(), m.group(2))]
    out = []
    for index, match in enumerate(headers):
        start = match.start()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(chunk)
        out.append((match.group(1), start, end, match.group(2).strip()))
    return out


def trim(text: str, start: int, end: int, anchor: str | None, max_chars: int) -> tuple[int, int, bool]:
    """Narrow an over-long section, cutting only at sentence boundaries."""
    if anchor:
        found = text.find(anchor, start, end)
        if found != -1:
            start = found
    if end - start <= max_chars:
        return start, end, False
    cut = text.rfind(". ", start, start + max_chars)
    return start, (cut + 1 if cut > start else start + max_chars), True


def _contains_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _infer_tags(field: str, text: str) -> list[str]:
    """Best-effort topic tags for an auto-discovered passage, so it can
    participate in assistant/merge.py's overlap/gap detection the same way a
    hand-tagged passage did. Reuses TAG_SYNONYMS (brand_agent/selector.py) -
    the same words a clinician's question would use to ask about a topic are a
    reasonable signal that a passage IS about that topic - plus a field-level
    hint for topics a subsection's own wording might not restate (a dosing
    subsection doesn't always say "dose")."""
    lowered = text.lower()
    tags = {
        tag for tag, synonyms in TAG_SYNONYMS.items()
        if _contains_word(lowered, tag.lower()) or any(_contains_word(lowered, w) for w in synonyms)
    }
    tags.update(_FIELD_TAG_HINTS.get(field, ()))
    return sorted(tags)


def _cut_passage(chunk: str, field: str, number: str, title: str, start: int, end: int,
                  anchor: str | None, max_chars: int) -> dict:
    """Verbatim slice + trim + source-offset bookkeeping for one passage."""
    header_preview = " ".join(chunk[start:start + 110].split())
    start, end, truncated = trim(chunk, start, end, anchor, max_chars)
    text = " ".join(chunk[start:end].split())
    return {
        "section": number,
        "title": title,
        "tags": _infer_tags(field, text),
        "text": text,
        "source": {
            "field": field,
            "char_start": start,
            "char_end": end,
            "truncated": truncated,
            # First ~110 chars of the section as the SPL renders it, so a
            # KNOWN display title can be checked against the real header.
            "header_preview": header_preview,
        },
    }


def discover_sections(record: dict, known: dict[tuple[str, str], dict], fields=AUTO_FIELDS) -> list[dict]:
    """Every numbered section openFDA's own SPL text exposes in `fields`. Where
    a section matches one in `known`, use its hand-verified title/anchor (and
    the tighter MAX_CHARS that anchor earns); every other section - including
    ones nobody has specifically looked at - still gets the same verbatim cut,
    just with the label's own first-word header capture as its title and a
    bigger, anchor-less budget (AUTO_MAX_CHARS)."""
    passages, seen = [], set()
    for field in fields:
        for chunk in record.get(field) or []:
            for number, start, end, regex_title in find_all_sections(chunk):
                key = (field, number)
                if key in seen:
                    continue
                seen.add(key)
                spec = known.get(key)
                title = spec["title"] if spec else regex_title
                anchor = spec.get("anchor") if spec else None
                max_chars = MAX_CHARS if spec else AUTO_MAX_CHARS
                passages.append(_cut_passage(chunk, field, number, title, start, end, anchor, max_chars))
    return passages


def build(drug: str) -> None:
    recipe = KNOWN[drug]
    record = fetch_spl(recipe["spl_id"])
    effective = record.get("effective_time", "unknown")

    sections = discover_sections(record, recipe["sections"])

    label = {
        "_meta": {
            "source": "openFDA / DailyMed Structured Product Label",
            "spl_id": recipe["spl_id"],
            "effective_time": effective,
            "retrieved": date.today().isoformat(),
            "verbatim": True,
            "note": (
                f"Demo agent serving the public DailyMed label for {drug}. Not operated by any "
                "manufacturer. Passages are verbatim slices of the source SPL, automatically "
                "discovered from every numbered contraindications/interactions/dosing/"
                "indications/geriatric-use section rather than hand-picked; section numbers and "
                "titles are read from the label text; char_start/char_end index into the named "
                "openFDA field; topic tags are inferred from the same word list a clinician's "
                "question is matched against."
            ),
        },
        "agent": {
            "drug": drug,
            "brand": drug.capitalize(),
            "displayName": recipe["display"],
            "subtitle": "demo agent serving the public DailyMed label, not operated by the manufacturer",
        },
        "labelVersion": f"SPL {effective}",
        "sections": sections,
    }

    out = ROOT / "labels" / f"{drug}.json"
    out.write_text(json.dumps(label, indent=2) + "\n")
    print(f"{drug}: {len(sections)} passages (auto-discovered) -> {out.relative_to(ROOT)}")


def label_age_hours(drug: str) -> float | None:
    """Hours since labels/<drug>.json was last built, or None if it doesn't exist
    or its retrieval date can't be read."""
    path = ROOT / "labels" / f"{drug}.json"
    if not path.exists():
        return None
    try:
        retrieved = date.fromisoformat(json.loads(path.read_text())["_meta"]["retrieved"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None
    return (date.today() - retrieved).days * 24.0


def refresh_if_stale(drug: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> bool:
    """Rebuild labels/<drug>.json from live openFDA if it's missing or older than
    max_age_hours. Returns True if a rebuild actually happened.

    Never raises: a failed fetch (network down, openFDA unavailable) is logged
    and the existing cached label is left in place, since a stale-but-present
    label always beats no label at all for an agent that's about to serve
    requests.
    """
    age = label_age_hours(drug)
    if age is not None and age < max_age_hours:
        return False
    try:
        build(drug)
        return True
    except Exception as exc:  # noqa: BLE001 - a failed refresh must not crash the agent
        print(f"[build_label] refresh failed for {drug}, keeping cached label: {exc}")
        return False


if __name__ == "__main__":
    for name in sys.argv[1:] or list(KNOWN):
        build(name)
