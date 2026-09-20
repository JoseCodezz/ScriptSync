# HCP Assistant (`assistant/`)

The doctor's side. It finds the label agents, verifies each one, asks them, checks their signatures, and merges
what comes back. The whole team reference is `CLAUDE.md`; this file only covers the assistant.

## Run it
Easiest, on Windows (starts the brand agents, impostors, this assistant and the web UI, and reports whether the
agents pass identity verification):

    .\scripts\start_all.ps1 -Restart

Just the assistant, from the repo root with the venv active:

    pip install -r requirements.txt
    uvicorn assistant.server:app --port 8080 --reload

Settings come from `.env` (copy `.env.example`; it is loaded automatically). `SCRIPTSYNC_DEMO=1` turns on the
presenter-only impostor tests; without it `/attack/*` returns 403 and impostors are hidden from `/agents`.

Try it: open http://127.0.0.1:8080/docs (interactive API page), or

    curl http://127.0.0.1:8080/health
    curl -X POST http://127.0.0.1:8080/ask -H "Content-Type: application/json" -d "{\"question\":\"Can simvastatin be taken with clarithromycin?\"}"
    curl -X POST http://127.0.0.1:8080/attack/lookalike      # demo mode only

## Endpoints
POST /ask, POST /attack/{lookalike|expired|replay|revoked} (demo mode only), GET /agents, GET/PUT /rules,
GET /log, POST /handoff, GET /health.

`GET /health` says what is real: `{verification: "live-dns", verificationDetail: {live: ["dns","cert"],
simulated: ["log","current"], dnssecBypass}, signing: "demo-hmac", demo}`. The web UI builds its "what is live"
wording from this, so keep it truthful.

## Contracts other branches rely on
- **Agent response** (POST <endpoint>/answer): `{agentName, brand, answers:[{section, title, labelVersion, text, tags, source}], refused, reason?, selectionMode, timestamp, signature}`.
- **Signing**: `common/signing.py` (`sign_response`) - HMAC with a demo key; disclose this. The brand agent must sign with it so the assistant's check passes.
- **Verification**: `assistant/verify.py::is_agent_verified(name, endpoint)` is the only ANS touchpoint. Two of the four checks are live (`dns`: read the agent's key from DNS; `cert`: the agent must sign a fresh challenge with that key). `log` and `current` are simulated and say so in their message. It fails closed. Results are cached (120 s if passed, 15 s if failed) and computed in parallel, off the event loop.
- **Tags** (label authors must use these spellings): CYP3A, interaction, dosing, indication, monitoring, liver, renal, pregnancy, older-adults, pediatric, switching, adverse-reactions, warnings, overdose, mechanism. (`switching` = a passage that actually addresses changing from one drug to another; most labels have none, so a switching question usually ends in "Not covered".)
- **Discovery**: `config/agents.json`. Add an entry, no code change. Each brand agent's `drug` (and optional `aliases`, e.g. `["zocor"]`) is how the assistant recognizes drug names in a question.
- **`/ask` response**: `{question, sources[], refused[], blocked[], unreachable[], skipped[], overlaps[], gaps[], notices[], analysis, disclaimer}`.
  - `notices[]`: `{type, message}`, e.g. `advice` when the question sounds like a request for a recommendation (the assistant warns and reframes; it still answers).
  - `analysis`: `{drugsMentioned[], drugsWithoutAgent[], topics[], switching, adviceSeeking}`.
  - `gaps[]` also carries `{topic: "drug: <name>", message}` for a drug named in a switching question that has no verified source.
- **No patient data**: `/ask`, `/attack/*` and `/handoff` return HTTP 400 if the text looks like it holds patient identifiers (SSN, phone, email, dates of birth, MRN, patient names, street addresses). Nothing is sent to an agent. The audit log never stores question text, only its length and a short fingerprint.
- **`patientContext`** (optional, on `/ask` and forwarded to each agent's `/answer`): non-identifying clinical details only - age range, renal/liver function, current medications (see `web/patient-context-template.txt`). Run through the same PHI check as the question, so a pasted identifier is still refused. Never logged - not even a fingerprint of it, unlike the question - and never persisted client-side; the web UI clears the field the instant a question is sent.

## Question understanding (assistant/understand.py)
Deterministic, no language model. Order of a `/ask`: PHI check, analyze, verify + ask agents (in parallel), merge, add drug gaps and notices.

## Tests
    python -m unittest tests.test_understand tests.test_demo_gate -v

`dev/mock_agent.py` still serves the four impostors. It is a scripted stand-in, not a real agent.
