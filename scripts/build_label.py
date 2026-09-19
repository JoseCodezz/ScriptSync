"""Build a brand agent's label file from the public FDA label (openFDA / DailyMed).

Passages are VERBATIM contiguous slices of the source SPL. Section numbers and
titles are read out of the label text itself rather than assigned by hand, so a
passage can never be filed under a section it did not come from. Each passage
records the openFDA field and character offsets it was cut from, so anyone can
re-fetch the SPL and check the quote.

    python scripts/build_label.py                  # both drugs
    python scripts/build_label.py simvastatin
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENFDA = "https://api.fda.gov/drug/label.json"
MAX_CHARS = 1500  # keeps a quote readable in the UI; always cut at a sentence end

# A section header inside SPL text: "4.5 Lomitapide, Lovastatin, and Simvastatin"
HEADER = re.compile(
    r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s+([A-Z][A-Za-z0-9,\-/()' ]{3,70}?)(?=\s+[A-Z(]|\s*$)"
)

# Tags MUST use the spellings fixed in CLAUDE.md - assistant/merge.py keys
# overlap and gap detection off them.
RECIPES = {
    "simvastatin": {
        "spl_id": "4bbbb5c9-6c29-bc3b-e063-6294a90a6cce",
        "display": "Simvastatin label agent",
        "passages": [
            {"field": "contraindications", "section": "4", "title": "Contraindications", "tags": ["CYP3A", "interaction"]},
            {"field": "drug_interactions", "section": "7.1", "title": "Drug Interactions that Increase the Risk of Myopathy and Rhabdomyolysis with Simvastatin", "anchor": "Strong CYP3A4 inhibitors",
             "tags": ["CYP3A", "interaction", "monitoring"]},
            {"field": "dosage_and_administration", "section": "2.1", "title": "Important Dosage and Administration Information", "tags": ["dosing"]},
            {"field": "dosage_and_administration", "section": "2.2", "title": "Recommended Dosage in Adult Patients", "tags": ["dosing"]},
            {"field": "dosage_and_administration", "section": "2.5", "title": "Dosage Modifications Due to Drug Interactions",
             "tags": ["dosing", "interaction"]},
            {"field": "indications_and_usage", "section": "1", "title": "Indications and Usage", "tags": ["indication"]},
            {"field": "geriatric_use", "section": "8.5", "title": "Geriatric Use", "tags": ["older-adults"]},
        ],
    },
    "clarithromycin": {
        "spl_id": "41392bb7-d916-446c-e063-6294a90a2c5c",
        "display": "Clarithromycin label agent",
        "passages": [
            {"field": "contraindications", "section": "4.5", "title": "Lomitapide, Lovastatin, and Simvastatin", "tags": ["CYP3A", "interaction"]},
            {"field": "drug_interactions", "section": "7", "title": "Drug Interactions - lipid-lowering agents", "anchor": "Lipid-lowering agents",
             "tags": ["CYP3A", "interaction", "monitoring"]},
            {"field": "contraindications", "section": "4.4", "title": "Colchicine", "tags": ["interaction", "renal"]},
            {"field": "dosage_and_administration", "section": "2.2", "title": "Adult Dosage", "tags": ["dosing"]},
            {"field": "dosage_and_administration", "section": "2.6", "title": "Dosage Adjustment in Patients with Renal Impairment", "tags": ["renal", "dosing"]},
            {"field": "dosage_and_administration", "section": "2.7", "title": "Dosage Adjustment Due to Drug Interactions",
             "tags": ["dosing", "interaction"]},
            {"field": "indications_and_usage", "section": "1.3", "title": "Community-Acquired Pneumonia", "tags": ["indication"]},
            {"field": "geriatric_use", "section": "8.5", "title": "Geriatric Use", "tags": ["older-adults", "renal"]},
        ],
    },
}


def fetch_spl(spl_id: str) -> dict:
    query = urllib.parse.quote(f'id:"{spl_id}"')
    with urllib.request.urlopen(f"{OPENFDA}?search={query}&limit=1", timeout=30) as response:
        return json.load(response)["results"][0]


def find_section(chunk: str, number: str) -> tuple[int, int, str] | None:
    """Locate a numbered section: returns (start, end, title) from the label's own header."""
    headers = list(HEADER.finditer(chunk))
    for index, match in enumerate(headers):
        if match.group(1) != number:
            continue
        start = match.start()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(chunk)
        return start, end, match.group(2).strip()
    return None


def trim(text: str, start: int, end: int, anchor: str | None) -> tuple[int, int, bool]:
    """Narrow an over-long section, cutting only at sentence boundaries."""
    if anchor:
        found = text.find(anchor, start, end)
        if found != -1:
            start = found
    if end - start <= MAX_CHARS:
        return start, end, False
    cut = text.rfind(". ", start, start + MAX_CHARS)
    return start, (cut + 1 if cut > start else start + MAX_CHARS), True


def extract(record: dict, recipe: dict) -> dict | None:
    for chunk in record.get(recipe["field"]) or []:
        located = find_section(chunk, recipe["section"])
        if not located:
            continue
        start, end, _header_title = located
        header_preview = " ".join(chunk[start:start + 110].split())
        start, end, truncated = trim(chunk, start, end, recipe.get("anchor"))
        passage = {
            "section": recipe["section"],
            "title": recipe["title"],
            "tags": recipe["tags"],
            "text": " ".join(chunk[start:end].split()),
            "source": {
                "field": recipe["field"],
                "char_start": start,
                "char_end": end,
                "truncated": truncated,
                # First ~110 chars of the section as the SPL renders it, so the
                # curated display title can be checked against the real header.
                "header_preview": header_preview,
            },
        }
        return passage
    return None


def build(drug: str) -> None:
    recipe = RECIPES[drug]
    record = fetch_spl(recipe["spl_id"])
    effective = record.get("effective_time", "unknown")

    sections, missing = [], []
    for spec in recipe["passages"]:
        found = extract(record, spec)
        if found:
            sections.append(found)
        else:
            missing.append(f"{spec['field']} §{spec['section']}")

    label = {
        "_meta": {
            "source": "openFDA / DailyMed Structured Product Label",
            "spl_id": recipe["spl_id"],
            "effective_time": effective,
            "retrieved": date.today().isoformat(),
            "verbatim": True,
            "note": (
                f"Demo agent serving the public DailyMed label for {drug}. Not operated by any "
                "manufacturer. Passages are verbatim slices of the source SPL; section numbers "
                "and titles are read from the label text; char_start/char_end index into the "
                "named openFDA field."
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
    print(f"{drug}: {len(sections)}/{len(recipe['passages'])} passages -> {out.relative_to(ROOT)}")
    if missing:
        print(f"  WARNING section not found: {', '.join(missing)}")


if __name__ == "__main__":
    for name in sys.argv[1:] or list(RECIPES):
        build(name)
