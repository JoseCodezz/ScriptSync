# ScriptSync agent layer

Three agents, built on this branch. Nothing here touches the work on
`assistant-core`, `frontend`, or `ans-verify` — the only integration point is
the `HCPAssistant` class, which a main program imports and calls.

```
                    physician types a question
                              │
                    ┌─────────▼──────────┐
                    │   HCP Assistant    │   agents/hcp_assistant/
                    │  intake →          │
                    │  fan out →         │   knows zero drug facts
                    │  synthesize        │
                    └─────┬────────┬─────┘
              resolve()   │        │   resolve()
                          │        │
         ┌────────────────▼┐      ┌▼────────────────┐
         │ Renavex agent   │      │ Glyvera agent   │   agents/drug_agent/
         │ :8001           │      │ :8002           │
         │ SGLT2 inhibitor │      │ GLP-1 RA        │   one drug each,
         └─────────────────┘      └─────────────────┘   signs every answer
```

Both drug agents are the *same code* — `agents/drug_agent/service.py` — run
twice against different monograph files. Adding a third drug is a data file plus
a registry entry; no code changes.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # required; not set yet on this machine

./run_agents.sh                            # terminal 1 — starts both drug agents

# terminal 2
.venv/bin/python -m agents.hcp_assistant \
    "Kidney function is sliding and he's off target. What should I add?" \
    --record examples/patient_case.md
```

Add `--raw` to see each drug agent's structured answer before synthesis.

Tests run without an API key (the model is stubbed, everything else is real):

```bash
.venv/bin/python -m tests.test_pipeline      # 26 passed
```

## Using it from a main program

```python
from agents.hcp_assistant import HCPAssistant

assistant = HCPAssistant()                     # reads agents/registry.json
result = await assistant.consult(hcp_prompt, patient_record_text=note)

result.brief                 # markdown brief for the physician
result.intake                # structured case + what data was missing
result.responses             # per-agent answers, each with a trust verdict
result.trusted_responses     # only the cryptographically verified ones
```

`HCPAssistant(agent_names=[...])` narrows which agents get consulted;
`http_transport=` injects a custom transport (mTLS, proxy, or in-process apps
for tests).

## How the three steps work

**Intake** turns the physician's free text plus any attached record into a
`CaseIntake` — a structured `PatientContext` and one question phrased so a
single-product specialist can answer it. It is told to leave fields null rather
than infer them, because downstream agents dose against those numbers. Anything
the physician left out that would change the answer lands in
`omitted_but_needed` and is surfaced back to them.

**Fan-out** resolves each ANS name to an endpoint, pulls the agent card, POSTs
the same question to every agent concurrently, and verifies the signed response
before accepting it. One failing agent does not fail the consultation — it comes
back as an `AgentResponse` with `error` set, and the synthesis prompt is told to
treat that product as unassessed rather than reasoning about it from memory.

**Synthesis** weighs the answers against each other. The prompt is explicit that
these agents are advocates for their own products and that the assistant's value
is in reading across them — and that it has no independent drug knowledge, so it
cannot fill a gap both agents left open.

### Why the drug agents are constrained the way they are

Each one gets exactly one monograph and is told it is the only source of truth.
The rules that matter:

- Every clinical claim carries a `supporting_evidence` citation with the dotted
  field path it came from, so any line in the brief is traceable to a source.
- Gaps go in `unknowns`. The agent is explicitly told that "not in my label"
  beats a plausible guess from training data — which is the failure mode that
  would make this whole architecture unsafe.
- The agent must surface contraindications that argue *against* its own product.
  `suitability: not_appropriate` is a valid and expected answer.
- It refuses to compare itself to competitors; comparison is the assistant's job.

The monograph sits behind a prompt-cache breakpoint at the front of the system
prompt, so repeat consultations against the same agent only pay for the changing
parts.

## Identity and trust — what's built, and where ANS plugs in

Every answer comes back in a `SignedEnvelope`: the payload, an Ed25519 signature
over its canonical JSON, and the signing key's fingerprint. The assistant
re-canonicalizes, checks the signature against the key in the agent's published
card, and confirms the card's key actually hashes to the claimed fingerprint. A
tampered payload or a substituted key is rejected — both are covered by tests.

Each agent publishes a card at `GET /.well-known/agent.json`:

```json
{
  "ans_name": "a2a://renavex.drug-information.corvus-therapeutics.v1.0.0",
  "capability": "drug-information",
  "endpoint": "http://127.0.0.1:8001",
  "public_key": "HlIJpkUWlVQbbKgeVE9FPFMMpJ+EdLIiT12/Gf5EbH4=",
  "key_fingerprint": "9848fd0e...59ec",
  "dns_anchor": {
    "record": "_ans.renavex.scriptsync.dev",
    "type": "TXT",
    "expected_value": "v=ans1; name=a2a://renavex...; alg=ed25519; key=sha256:9848fd0e...59ec"
  },
  "data_scope": "Renavex (glifozastat) only. No knowledge of competing products."
}
```

That signature proves *the same key* signed the answer and the card. It does not
yet prove the key belongs to Corvus Therapeutics — a lookalike agent could
generate its own keypair and claim the same name. Closing that gap is what DNS
is for, and it is the one piece still to wire up.

### Naming

Names follow the ANS shape `protocol://AgentID.Capability.Provider.vMAJOR.MINOR.PATCH`,
parsed and validated in `agents/common/identity.py`:

| segment | value |
|---|---|
| protocol | `a2a` |
| agent ID | `renavex` |
| capability | `drug-information` |
| provider | `corvus-therapeutics` |
| version | `v1.0.0` |

The capability segment is what makes discovery work later: a core assistor asks
for *"something that does `drug-information`"* rather than naming agents it was
hardcoded to know about.

### Resolution

`agents/common/registry.py` is the seam. `resolve(name) -> endpoint` reads
`agents/registry.json` today, but checks `ANS_RESOLVER_URL` first:

```python
# already in resolve()
resolver_url = os.environ.get("ANS_RESOLVER_URL")
if resolver_url:
    response = httpx.get(f"{resolver_url}/resolve", params={"name": ans_name})
    return response.json()["endpoint"]
```

Pointing that env var at a real ANS registry swaps local resolution for real
resolution without touching the assistant or the drug agents.

## Next: the GoDaddy DNS piece

The verification code is written and wired — `check_dns_anchor()` in
`agents/common/registry.py` resolves the TXT record and confirms it pins the
agent's key fingerprint. It is gated behind `ANS_DNS_VERIFY=1` and currently
reports "skipped", because there is no real domain yet. Turning it on is three
steps:

**1. Print the TXT records to publish.** Each agent knows the exact string:

```bash
.venv/bin/python -c "
from agents.common.identity import AgentIdentity
import json, pathlib
for f in ['company_a.json', 'company_b.json']:
    m = json.loads(pathlib.Path('data', f).read_text())
    i = AgentIdentity.load_or_create(m['agent']['ans_name'])
    print(f\"_ans.{m['product']['brand_name'].lower()}  TXT  {i.dns_txt_value()}\")"
```

**2. Add them in GoDaddy.** In DNS Management for the domain, add a TXT record
per agent — host `_ans.renavex`, value the `v=ans1; ...` string. The underscore
prefix keeps them out of the way of normal hostnames, the same convention SPF
and DKIM use. Propagation is usually minutes; confirm with
`dig +short TXT _ans.renavex.yourdomain.com`.

**3. Run the agents against that domain and turn verification on:**

```bash
python -m agents.drug_agent --data data/company_a.json --port 8001 \
    --dns-domain yourdomain.com
ANS_DNS_VERIFY=1 python -m agents.hcp_assistant "..." --record examples/patient_case.md
```

Now `verification.dns_anchored` becomes `true`/`false` instead of `null`, and a
DNS check that *fails* drops `trusted` to false — the assistant still shows the
answer but the synthesis prompt is told to discount it.

### What that does and does not prove

It proves whoever controls the DNS zone vouches for that signing key. For a
hackathon demo where the threat is "an agent impersonating a pharma company's
medical information service," that is the right control and it is checkable live
on stage.

It is weaker than what a production ANS would do, in two ways worth naming out
loud: without DNSSEC the TXT lookup itself is spoofable by an on-path attacker,
and DNS gives no revocation story — pulling a compromised key means editing a
record and waiting out the TTL. Real ANS designs put a CA and certificate
lifecycle behind this. The DNS anchor is a good demo-grade stand-in and the code
is structured so swapping in certificate validation touches only
`check_dns_anchor()`.

### Open questions for the ANS work

- Which ANS spec are we targeting, and does the core assistor discover agents by
  capability or by exact name? The capability segment is already in the names
  either way.
- Does the registry hand back the public key alongside the endpoint? If so the
  card fetch becomes a consistency check rather than the trust root, which is
  stronger.
- Registration and renewal: who owns the agent's entry, and what happens when
  `v1.0.0` becomes `v1.1.0` — does the assistant pin a major version?

## A note on the data

`data/company_a.json` and `data/company_b.json` are **synthetic**. Brand names,
generic names, trial names and every number in them are invented. Both files
carry a `_meta.synthetic` flag, every agent card reports `synthetic_data: true`,
and that flag is passed into the synthesis prompt. Swapping in real monograph
data means replacing the files — the schema is the contract, not the content.
