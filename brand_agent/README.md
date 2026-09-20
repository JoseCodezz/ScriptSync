# Brand agents (`labels-brand-agent`)

Two agents, one per drug. Each answers **only** from its own approved FDA label,
signs every answer, and carries an ANS identity that can be verified
independently. They replace `dev/mock_agent.py`.

| agent | port | label source |
|---|---|---|
| Simvastatin label agent | 9001 | DailyMed SPL `4bbbb5c9…`, effective 2026-02-26 |
| Clarithromycin label agent | 9002 | DailyMed SPL `41392bb7…`, effective 2025-10-15 |

The pair is deliberate: **each label independently names the other drug as a
contraindication.** Simvastatin §4 contraindicates strong CYP3A4 inhibitors
"(e.g., erythromycin and clarithromycin)"; clarithromycin §4.5 contraindicates
"Lomitapide, Lovastatin, and Simvastatin". Two agents that have never heard of
each other converge on the same finding, and a judge can check both on DailyMed.

## Running

```bash
.venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...          # optional, see "No API key" below

./scripts/run_brand_agents.sh                # terminal 1
.venv/bin/python -m uvicorn assistant.server:app --port 8080   # terminal 2
# terminal 3: serve web/ on 5500
```

Tests need no API key:

```bash
.venv/bin/python -m tests.test_brand_agent                  # 41 checks
VERIFY_VERBATIM=1 .venv/bin/python -m tests.test_brand_agent # + re-checks every
                                    # quote against live openFDA (55 checks)
```

## How an answer is produced

The model **selects sections; it never writes text.** It sees the label and
returns section numbers; the server looks the verbatim passage up out of
`labels/<drug>.json`. Nothing the model generates reaches the clinician.

That is what lets the agent reason about a specific patient — "this patient is
on an interacting drug, so §7.1 is relevant" — without any risk of it authoring
a medical claim. `tests/test_brand_agent.py` asserts every returned passage is
a string present in the label file.

**No API key?** `selector.py` falls back to deterministic keyword matching and
the demo still runs — the response carries `selectionMode: "keyword-fallback"`
so it is never silently degraded. Both paths were tested. Patient context
(`patientContext` - age, renal/liver function, current medications; see
`web/patient-context-template.txt`) works in fallback mode too: it's matched
the same way the question is (tags, then title words), plus one extra step -
a named "Current medications" drug is checked against a section's own
verbatim text, not just its title, since a specific drug name doesn't carry
the false-positive risk that ruled out body-text matching for everything
else. This is genuinely the path this demo runs on without a real key, not
just a safety net - worth saying so explicitly when presenting it.

## Label data

`labels/*.json` is built by `scripts/build_label.py` from openFDA. Sections are
**auto-discovered, not hand-picked**: every numbered section in the
contraindications, drug interactions, dosing, indications, and geriatric-use
fields becomes its own passage, whatever its number. `scripts/build_label.py`'s
`KNOWN` dict still seeds a hand-verified title (and, for a couple of unusually
long sections, an anchor point past their preamble) for the sections someone
already looked at closely - but it no longer gates what gets extracted the way
the old hand-picked recipe list did. Rules it enforces:

- Passages are **verbatim contiguous slices** of the SPL. No paraphrase, no
  hand-written text.
- Section numbers and titles are read from the label's own headers, so a passage
  cannot be filed under a section it did not come from. A header has to sit at
  a real clause boundary and not be immediately followed by a unit word
  ("10 Years", "40 mg") - both catch numbers embedded in running prose or a
  flattened table that would otherwise look like a header. Each passage stores
  `source.header_preview` — the label's real header — so a KNOWN display title
  can be checked against it.
- Every passage records `field`, `char_start`, `char_end`, so any quote can be
  re-derived from openFDA. That is what `VERIFY_VERBATIM=1` does.
- Tags are **inferred**, not hand-typed: `_infer_tags` reuses
  `brand_agent/selector.py`'s `TAG_SYNONYMS` (the same words a clinician's
  question is matched against) against each passage's own text, plus a
  field-level hint (anything in `dosage_and_administration` is at least
  "dosing", etc.). Tags use the exact spellings in CLAUDE.md; `assistant/merge.py`
  keys overlap and gap detection off them. The test fails on an unknown tag.

To re-pull after a label update: `python scripts/build_label.py`. Running an
agent (`python -m brand_agent ...`) also does this itself, occasionally: it
checks staleness at startup and every `FDA_REFRESH_CHECK_HOURS` (default 6h)
after that, and only actually re-fetches when the cached label is older than
`FDA_REFRESH_MAX_AGE_HOURS` (default 24h) - see `scripts/build_label.py`'s
`refresh_if_stale`. A failed fetch just keeps serving the existing cached
label; it never crashes the agent. `service.py` re-reads `labels/<drug>.json`
off disk on a 60s TTL, so a background refresh reaches a running agent without
a restart.

## Wire contract

Implements the contract in CLAUDE.md, so the assistant and web UI work unchanged:

```
GET  /identity        -> {agentName, ...ANS identity block}
POST /answer          -> {agentName, brand, answers[{section, title, labelVersion,
                          text, tags, source}], refused, reason?, timestamp, signature}
POST /ans/challenge   -> {agentName, challenge, signature, publicKey}
GET  /health
```

`/answer` also accepts an optional `patientContext` string. Older callers
sending only `{question}` are unaffected. Answers are HMAC-signed with
`common/signing.py`, exactly as the assistant expects.

## ANS identity

Domain: **scriptsync.health** (registered at Porkbun). Each agent holds an
Ed25519 key and implements the `ans-verify` protocol exactly:

| | value |
|---|---|
| TXT host | `_agentid.<agent>.scriptsync.health` |
| TXT value | `v=agentkey1; k=<base64 SPKI DER public key>` |
| signed payload | `agent-identity-v1\|domain\|agent\|challenge\|issuedAt` |
| endpoint | `POST /ans/challenge {domain, agent, challenge, issuedAt}` |

The **full public key** goes in DNS, not a fingerprint. That means a verifier
needs nothing but a public DNS lookup and never has to contact or trust the
agent. `/ans/challenge` rebuilds the payload from its components and refuses to
sign for any identity but its own — blind-signing a caller-supplied string would
let that caller obtain a signature over text of their choosing.

### Publishing the records

```bash
ANS_DOMAIN=scriptsync.health .venv/bin/python scripts/ans_records.py
```

Add each one in **Porkbun → Details → DNS Records** (type TXT, host
`_agentid.simvastatin`, answer the `v=agentkey1; …` string, TTL 600). Confirm:

```bash
dig +short TXT _agentid.simvastatin.scriptsync.health
```

Note: `ans-verify/src/godaddy/dns.ts` publishes via the **GoDaddy API**, which
cannot write to a Porkbun-hosted zone. Publish by hand, or point the code at
Porkbun's API. Verification is unaffected — `lookup.ts` reads public DNS and is
registrar-agnostic.

### Proving it works

```bash
./scripts/run_brand_agents.sh
.venv/bin/python scripts/ans_verify_live.py
```

A standalone third-party verifier: issues a challenge, has the agent sign it,
reads the key **from DNS**, verifies, and checks that a replayed signature fails
for a different round. Works without the Node service, which is still
`forceError;` in `src/server.ts`.

### Honest limits

DNS proves whoever controls the zone vouches for the key. Without DNSSEC the
lookup is spoofable on-path, and DNS has no revocation — pulling a key means
editing a record and waiting out the TTL. A production ANS puts a CA and
certificate lifecycle behind this. The "simulated vs live" honesty rule in
CLAUDE.md applies.


## Notes for the rest of the team

- `config/agents.json` and `config/rules.json` were updated: the two brand
  entries now point at the real agents, `allowedBrands` is
  `["Simvastatin", "Clarithromycin"]`, and the **attacker entries were
  re-pointed** to imitate the simvastatin agent — otherwise the lookalike demo
  compares against a name that no longer exists.
- `dev/mock_agent.py` still serves the attackers on 9101-9104. Pass the
  attacker's ANS name explicitly, e.g.
  `--name "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync-labels.example"`.
- Now that every numbered section is auto-discovered rather than hand-picked,
  simvastatin's own §2.4 renal-dosing section surfaces too - the old "ask about
  kidneys and simvastatin says not covered" demo beat no longer holds. Pick a
  genuinely uncovered topic (pregnancy still works for both) if you need a
  live "not covered" moment.
