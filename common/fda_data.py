"""Fetch FDA data: a raw URL reader, and a search helper over the openFDA API.

Shared fetch layer for anything in this repo that pulls live FDA/clinical data.
`scripts/build_label.py` uses `fetch_url_text` to pull the SPL record it slices
into brand-agent label passages; `query_openfda` / `research_clinical` are
broader research helpers available for future agents.
"""

import datetime
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "UpdateFDAData/1.0"


def fetch_url_text(url: str) -> str:
    """Fetch a URL and return its entire body as text.

    On any failure (bad URL, network error, non-2xx response, timeout, etc.)
    this returns an error string instead of raising, so callers can just
    print the result directly.
    """
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except Exception as error:
        return f"Try Again, {error}"


# openFDA endpoints grouped into categories. When researching a topic we
# fan out across every endpoint in its category (i.e. all the "relevant
# urls" for that kind of query), not just one.
# Full list of endpoints: https://open.fda.gov/apis/
_CATEGORY_ENDPOINTS = {
    "drug": ["drug/label", "drug/event", "drug/enforcement", "drug/ndc", "drug/drugsfda"],
    "device": ["device/enforcement", "device/event", "device/510k", "device/classification"],
    "food": ["food/enforcement"],
    "animal": ["animalandveterinary/event"],
    "tobacco": ["tobacco/problem"],
}

# Keywords that steer a free-text query toward a non-default category.
_CATEGORY_KEYWORDS = {
    "device": ["device", "implant", "510k", "medical device"],
    "food": ["food", "beverage", "dietary supplement", "allergen"],
    "animal": ["animal", "veterinary", "pet"],
    "tobacco": ["tobacco", "cigarette", "vape", "e-cigarette"],
}

# The field(s) each endpoint actually names its subject by. Searching these
# first (an exact match) avoids the false positives you get from openFDA's
# default full-text search, e.g. "ibuprofen" matching a Naproxen label just
# because ibuprofen is mentioned in its drug-interaction warnings.
_NAME_FIELDS = {
    "drug/label": ["openfda.brand_name", "openfda.generic_name", "openfda.substance_name"],
    "drug/event": [
        "patient.drug.openfda.brand_name",
        "patient.drug.openfda.generic_name",
        "patient.drug.medicinalproduct",
    ],
    "drug/enforcement": ["openfda.brand_name", "openfda.generic_name", "product_description"],
    "drug/ndc": ["brand_name", "generic_name"],
    "drug/drugsfda": ["openfda.brand_name", "openfda.generic_name", "products.brand_name"],
    "device/enforcement": ["product_description"],
    "device/event": ["device.brand_name", "device.generic_name"],
    "device/510k": ["device_name"],
    "device/classification": ["device_name"],
    "food/enforcement": ["product_description"],
    "animalandveterinary/event": ["animal.species"],
    "tobacco/problem": ["product_problems"],
}

# The fields worth surfacing for each endpoint, in priority order.
_RELEVANT_FIELDS = {
    "drug/label": [
        "openfda.brand_name",
        "openfda.generic_name",
        "purpose",
        "indications_and_usage",
        "warnings",
    ],
    "drug/event": ["patient.drug", "patient.reaction", "serious", "receivedate"],
    "drug/enforcement": [
        "product_description",
        "reason_for_recall",
        "classification",
        "status",
        "recall_initiation_date",
    ],
    "drug/ndc": ["brand_name", "generic_name", "dosage_form", "route", "active_ingredients"],
    "drug/drugsfda": ["products", "sponsor_name", "application_number"],
    "device/enforcement": ["product_description", "reason_for_recall", "classification", "status"],
    "device/event": ["device", "event_type", "date_received"],
    "device/510k": ["device_name", "applicant", "decision_date"],
    "device/classification": ["device_name", "medical_specialty_description", "device_class"],
    "food/enforcement": ["product_description", "reason_for_recall", "classification", "status"],
    "animalandveterinary/event": ["animal", "reaction", "onset_date"],
    "tobacco/problem": ["report_id", "date_submitted", "problems"],
}


def _pick_category(query: str) -> str:
    lowered = query.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return "drug"  # most free-text queries are about a drug


def _dig(record, dotted_path: str):
    """Walk a dotted path like 'openfda.brand_name' through nested dict/list JSON."""
    value = record
    for part in dotted_path.split("."):
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _flatten(value, prefix: str = "") -> dict:
    """Flatten a JSON record into {dotted.path: readable value}, dropping empties.

    Lists of scalars are joined into one string; lists of dicts are expanded
    with an index in the path. This is the "text map" used to surface fields
    that _RELEVANT_FIELDS doesn't know to look for.
    """
    items = {}
    if isinstance(value, dict):
        for key, sub_value in value.items():
            sub_prefix = f"{prefix}.{key}" if prefix else key
            items.update(_flatten(sub_value, sub_prefix))
    elif isinstance(value, list):
        if not value:
            return items
        if any(isinstance(item, (dict, list)) for item in value):
            for index, item in enumerate(value):
                items.update(_flatten(item, f"{prefix}.{index}"))
        else:
            joined = "; ".join(str(item) for item in value if item not in (None, ""))
            if joined:
                items[prefix] = joined
    elif value not in (None, ""):
        items[prefix] = value
    return items


def _build_precise_search(endpoint: str, query: str) -> str:
    """Build a search clause targeting the endpoint's actual name field(s).

    e.g. for drug/label: (openfda.brand_name:"ibuprofen" OR
    openfda.generic_name:"ibuprofen" OR openfda.substance_name:"ibuprofen")
    """
    quoted = query.replace('"', "")
    fields = _NAME_FIELDS.get(endpoint, [])
    if not fields:
        return quoted
    if len(fields) == 1:
        return f'{fields[0]}:"{quoted}"'
    return "(" + " OR ".join(f'{field}:"{quoted}"' for field in fields) + ")"


def _format_results(endpoint: str, results: list, match_type: str) -> str:
    fields = _RELEVANT_FIELDS.get(endpoint, [])
    lines = [f"-- {endpoint} [{match_type}] --"]
    for i, record in enumerate(results, start=1):
        lines.append(f"[{i}]")
        shown = set()
        for field in fields:
            value = _dig(record, field)
            if value:
                lines.append(f"  {field}: {value}")
                shown.add(field)

        extras = {k: v for k, v in _flatten(record).items() if k not in shown}
        for key, value in sorted(extras.items()):
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _research_endpoint(query: str, endpoint: str, limit: int) -> str:
    """Fetch one endpoint for `query`, preferring an exact name-field match
    and only falling back to a fuzzy full-text search (clearly labeled) if
    that comes up empty.
    """

    def _fetch(search_clause: str):
        url = f"https://api.fda.gov/{endpoint}.json?search={urllib.parse.quote(search_clause)}&limit={limit}"
        raw = fetch_url_text(url)
        if raw.startswith("Try Again, "):
            return None, raw
        try:
            return json.loads(raw), None
        except json.JSONDecodeError as error:
            return None, f"Try Again, {error}"

    payload, error = _fetch(_build_precise_search(endpoint, query))
    match_type = "exact match"

    if error or "error" in (payload or {}) or not (payload or {}).get("results"):
        payload, error = _fetch(query)
        match_type = "broad text match, may only mention the term"

    if error:
        return f"-- {endpoint} --\n{error}"
    if "error" in payload:
        return f"-- {endpoint} --\nNo data ({payload['error'].get('message', 'no matches')})"

    results = payload.get("results", [])
    if not results:
        return f"-- {endpoint} --\nNo results"

    return _format_results(endpoint, results, match_type)


def query_openfda(query: str, endpoint: str = None, limit: int = 3) -> str:
    """Research `query` across every openFDA endpoint relevant to it.

    Picks a category (drug/device/food/animal/tobacco) from keywords in
    `query`, unless `endpoint` pins it to one specific endpoint, then fans
    out across every endpoint in that category -- e.g. for a drug that
    means label, adverse events, recalls, NDC, and Drugs@FDA all at once --
    so the combined output is enough for an AI agent reading it to be a
    specialist on the topic, not just answer one narrow question about it.

    Each endpoint search first tries an exact match on its actual name
    field(s) (e.g. openfda.generic_name) and only falls back to a fuzzy
    full-text search, clearly labeled as such, if that turns up nothing --
    avoiding false positives like "ibuprofen" matching a Naproxen label
    just because ibuprofen is named in its warnings text.
    """
    endpoints = [endpoint] if endpoint else _CATEGORY_ENDPOINTS[_pick_category(query)]

    sections = [_research_endpoint(query, ep, limit) for ep in endpoints]
    header = f'=== openFDA report for "{query}" ==='
    return "\n\n".join([header] + sections)


def _fetch_json(url: str):
    """fetch_url_text + json.loads, returning (payload, error) instead of raising."""
    raw = fetch_url_text(url)
    if raw.startswith("Try Again, "):
        return None, raw
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as error:
        return None, f"Try Again, {error}"


def _strip_html(page: str) -> str:
    """Turn an HTML page into readable text (no external deps, so this is
    approximate: it drops tags/scripts/styles and unescapes entities, but
    keeps boilerplate nav/footer text along with the real content).
    """
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _cpic_pgx_report(drug: str) -> str:
    """Real, structured pharmacogenomic guidance from CPIC (api.cpicpgx.org) --
    a nonprofit consortium, not a scrape or an approximation.
    """
    drugs, error = _fetch_json(
        f"https://api.cpicpgx.org/v1/drug?name=eq.{urllib.parse.quote(drug.lower())}&limit=1"
    )
    if error:
        return f"  {error}"
    if not drugs:
        return "  No CPIC pharmacogenomic guideline is published for this drug."

    drug_id = drugs[0]["drugid"]
    pairs, error = _fetch_json(
        f"https://api.cpicpgx.org/v1/pair?drugid=eq.{urllib.parse.quote(drug_id, safe='')}&removed=eq.false"
    )
    if error:
        return f"  {error}"
    if not pairs:
        return "  No CPIC gene-drug pairs found for this drug."

    lines = []
    for pair in pairs:
        guideline, g_error = _fetch_json(
            f"https://api.cpicpgx.org/v1/guideline?id=eq.{pair['guidelineid']}"
        )
        name = guideline[0]["name"] if guideline and not g_error else "?"
        url = guideline[0]["url"] if guideline and not g_error else ""
        lines.append(
            f"  Gene: {pair['genesymbol']} | CPIC level: {pair.get('cpiclevel')} | "
            f"PGx testing: {pair.get('pgxtesting')} | Guideline: {name} ({url})"
        )
    return "\n".join(lines)


def _clinical_trials_report(query: str, limit: int = 3) -> str:
    """Raw trial listings from ClinicalTrials.gov -- source data for
    off-label use and comparative efficacy, not a curated NNT/NNH analysis.
    """
    url = f"https://clinicaltrials.gov/api/v2/studies?query.term={urllib.parse.quote(query)}&pageSize={limit}"
    payload, error = _fetch_json(url)
    if error:
        return f"  {error}"

    studies = payload.get("studies", [])
    if not studies:
        return "  No matching trials found."

    lines = []
    for study in studies:
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        lines.append(
            f"  {ident.get('nctId')}: {ident.get('briefTitle')} "
            f"[{status.get('overallStatus')}, phase={design.get('phases')}]"
        )
    return "\n".join(lines)


def _lactmed_report(drug: str) -> str:
    """Full LactMed monograph text (NIH's authoritative lactation database),
    fetched via fetch_url_text and stripped of HTML.
    """
    search_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=books&term="
        f"{urllib.parse.quote(drug)}%5Btitle%5D+AND+lactmed%5Bbook%5D&retmode=json"
    )
    payload, error = _fetch_json(search_url)
    if error:
        return f"  {error}"

    ids = payload.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return "  No LactMed monograph found for this drug."

    summary_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=books&id={ids[0]}&retmode=json"
    summary, error = _fetch_json(summary_url)
    if error:
        return f"  {error}"

    accession = summary["result"][ids[0]]["chapteraccessionid"]
    page = fetch_url_text(f"https://www.ncbi.nlm.nih.gov/books/{accession}/")
    if page.startswith("Try Again, "):
        return f"  {page}"
    return _strip_html(page)


def _dailymed_latest_label(drug: str, limit: int = 5) -> str:
    """The most recently published SPL label(s) for `drug` from DailyMed.

    DailyMed is NLM's own source-of-truth for drug labeling and updates the
    same day a manufacturer submits a new label, unlike openFDA's drug/label
    endpoint, which mirrors DailyMed on a periodic sync and can lag behind
    it. Lists the recent matches, then fetches the full current label text
    of the single most recently published one.
    """
    search_url = (
        "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?"
        f"drug_name={urllib.parse.quote(drug)}&pagesize={limit}"
    )
    payload, error = _fetch_json(search_url)
    if error:
        return f"  {error}"

    entries = payload.get("data", [])
    if not entries:
        return "  No DailyMed label found for this drug."

    def _parse_date(entry):
        try:
            return datetime.datetime.strptime(entry["published_date"], "%b %d, %Y")
        except (ValueError, KeyError):
            return datetime.datetime.min

    newest = max(entries, key=_parse_date)

    lines = [f"  {e['published_date']} | {e['title']} | setid={e['setid']}" for e in entries]

    page = fetch_url_text(
        f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={newest['setid']}"
    )
    if not page.startswith("Try Again, "):
        lines.append("")
        lines.append(f"  -- full current label text for the newest match ({newest['published_date']}) --")
        lines.append(_strip_html(page))

    return "\n".join(lines)


def _faers_signal_report(drug: str, top_n: int = 5) -> str:
    """Raw FAERS adverse-event report counts from openFDA -- NOT a validated
    ROR/EBGM disproportionality score, which requires a background-rate
    comparison this endpoint doesn't provide.
    """
    search_clause = _build_precise_search("drug/event", drug)
    url = (
        f"https://api.fda.gov/drug/event.json?search={urllib.parse.quote(search_clause)}"
        "&count=patient.reaction.reactionmeddrapt.exact"
    )
    payload, error = _fetch_json(url)
    if error:
        return f"  {error}"
    if "error" in payload:
        return f"  No adverse-event report data ({payload['error'].get('message', 'no matches')})"

    results = payload.get("results", [])[:top_n]
    lines = ["  Raw FAERS report counts (NOT a validated ROR/EBGM signal score):"]
    for result in results:
        lines.append(f"    {result['term']}: {result['count']} reports")
    return "\n".join(lines)


def research_clinical(query: str, limit: int = 3) -> str:
    """Pull whatever real clinical data exists for `query` from CPIC
    (pharmacogenomics), ClinicalTrials.gov (trial evidence), LactMed
    (lactation), and openFDA's FAERS counts -- concatenated as-is, with no
    section headers or category scaffolding wrapped around it.
    """
    parts = [
        _dailymed_latest_label(query, limit),
        _cpic_pgx_report(query),
        _clinical_trials_report(query, limit),
        _lactmed_report(query),
        _faers_signal_report(query, limit),
    ]
    return "\n\n".join(part for part in parts if part.strip())


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(query_openfda(query))
        print()
        print(research_clinical(query))
    else:
        print("Usage: python -m common.fda_data <search terms>")
