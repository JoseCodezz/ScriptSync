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
so it is never silently degraded. Both paths were tested.

## Label data

`labels/*.json` is built by `scripts/build_label.py` from openFDA. Rules it
enforces:

- Passages are **verbatim contiguous slices** of the SPL. No paraphrase, no
  hand-written text.
- Section numbers and titles are read from the label's own headers, so a passage
  cannot be filed under a section it did not come from. Each passage stores
  `source.header_preview` — the label's real header — so the display title can
  be checked against it.
- Every passage records `field`, `char_start`, `char_end`, so any quote can be
  re-derived from openFDA. That is what `VERIFY_VERBATIM=1` does.
- Tags use the exact spellings in CLAUDE.md; `assistant/merge.py` keys overlap
  and gap detection off them. The test fails on an unknown tag.

To re-pull after a label update: `python scripts/build_label.py`.

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

## ANS identity — what works, and the one thing left

Each agent holds an Ed25519 key and serves:

```
"ans": {
  "publicKey": "MCowBQYDK2VwAyEA…",      base64 SPKI DER
  "keyFingerprint": "5e7bc620…",
  "dnsRecord": "_ans.simvastatin.<domain>",
  "dnsTxtValue": "v=ans1; name=a2a://…; alg=ed25519; key=sha256:5e7bc620…",
  "dnsAnchored": null,
  "dnsDetail": "scriptsync.example is a placeholder; no domain registered yet"
}
```

`POST /ans/challenge` signs a verifier-supplied nonce, so a verifier proves the
agent **holds the key right now** rather than replayed an old signature. Tested
live over HTTP, including nonce reuse and cross-agent key substitution.

**Key encoding is base64 SPKI DER**, matching `generateAgentKeyPair()` in
`ans-verify/src/crypto/agentKeys.ts`. Raw 32-byte encoding would not
interoperate — worth knowing before both halves are finished.

`dnsAnchored` is **`null` = unknown, never a pass.** It only becomes `true` or
`false` once a real domain exists. Three steps:

1. Register a domain in GoDaddy. Print the records:
   ```bash
   ANS_DOMAIN=yourdomain.com .venv/bin/python -c "
   from pathlib import Path; import json
   from brand_agent.ans import ANSName, AgentIdentity
   for d in ('simvastatin','clarithromycin'):
       n = ANSName.build(d, 'yourdomain.com')
       i = AgentIdentity.load_or_create(n)
       print(f'{n.txt_record}  TXT  {i.txt_value()}')"
   ```
2. In GoDaddy DNS Management add one TXT record per agent — host
   `_ans.simvastatin`, value the `v=ans1; …` string. Confirm with
   `dig +short TXT _ans.simvastatin.yourdomain.com`.
3. Run with the real domain and turn verification on:
   ```bash
   ANS_DOMAIN=yourdomain.com ./scripts/run_brand_agents.sh
   ANS_DNS_VERIFY=1 .venv/bin/python -m uvicorn assistant.server:app --port 8080
   ```
   Update the `ansName` values in `config/agents.json` to the new domain.

### Honest limits

DNS proves whoever controls the zone vouches for the key. Without DNSSEC the
lookup is spoofable by an on-path attacker, and DNS has no revocation story —
pulling a key means editing a record and waiting out the TTL. A production ANS
puts a CA and certificate lifecycle behind this. Say so in the pitch; the
"simulated / live" honesty rule in CLAUDE.md covers this too.

## Notes for the rest of the team

- `config/agents.json` and `config/rules.json` were updated: the two brand
  entries now point at the real agents, `allowedBrands` is
  `["Simvastatin", "Clarithromycin"]`, and the **attacker entries were
  re-pointed** to imitate the simvastatin agent — otherwise the lookalike demo
  compares against a name that no longer exists.
- `dev/mock_agent.py` still serves the attackers on 9101-9104. Pass the
  attacker's ANS name explicitly, e.g.
  `--name "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync-labels.example"`.
- Simvastatin's SPL has no renal dosing section and clarithromycin's does. Ask
  about kidneys and you get one answer plus one honest "not covered" — a real
  demonstration of gap detection rather than a staged one.
