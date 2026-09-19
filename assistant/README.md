# HCP Assistant (assistant-core)

Run from the repo root, venv active:

    pip install -r requirements.txt
    .\scripts\start_mocks.ps1                       # temporary mock agents (ports 9001, 9002, 9101-9104)
    uvicorn assistant.server:app --port 8080 --reload

Try it: open http://127.0.0.1:8080/docs (interactive API page), or

    curl http://127.0.0.1:8080/health
    curl -X POST http://127.0.0.1:8080/ask -H "Content-Type: application/json" -d "{\"question\":\"Patient is on Drug A. I am considering Drug B. What should I know?\"}"
    curl -X POST http://127.0.0.1:8080/attack/lookalike

## Endpoints
POST /ask, POST /attack/{lookalike|expired|replay|revoked}, GET /agents, GET/PUT /rules,
GET /log, POST /handoff, GET /health.

## Contracts other branches rely on
- **Agent response** (POST <endpoint>/answer): `{agentName, brand, answers:[{section, labelVersion, text, tags, title?}], refused, reason?, timestamp, signature}`.
- **Signing**: `common/signing.py` (`sign_response`) - HMAC with a demo key; disclose this. The brand agent must sign with it so the assistant's check passes.
- **Verification**: `assistant/verify.py::is_agent_verified(name)` is the only ANS touchpoint. Simulated for now, reads `identity_stub` in `config/agents.json`.
- **Tags** (label authors must use these spellings): CYP3A, interaction, dosing, indication, monitoring, liver, renal, pregnancy, older-adults, pediatric, switching. (`switching` = a passage that actually addresses changing from one drug to another; most labels have none, so a switching question usually ends in "Not covered".)
- **Discovery**: `config/agents.json`. Add an entry, no code change. Each brand agent's `drug` (and optional `aliases`, e.g. `["zocor"]`) is how the assistant recognizes drug names in a question.
- **`/ask` response**: `{question, sources[], refused[], blocked[], unreachable[], skipped[], overlaps[], gaps[], notices[], analysis, disclaimer}`.
  - `notices[]`: `{type, message}`, e.g. `advice` when the question sounds like a request for a recommendation (the assistant warns and reframes; it still answers).
  - `analysis`: `{drugsMentioned[], drugsWithoutAgent[], topics[], switching, adviceSeeking}`.
  - `gaps[]` also carries `{topic: "drug: <name>", message}` for a drug named in a switching question that has no verified source.
- **No patient data**: `/ask`, `/attack/*` and `/handoff` return HTTP 400 if the text looks like it holds patient identifiers (SSN, phone, email, dates of birth, MRN, patient names, street addresses). Nothing is sent to an agent. The audit log never stores question text, only its length and a short fingerprint.

## Question understanding (assistant/understand.py)
Deterministic, no language model. Order of a `/ask`: PHI check, analyze, verify + ask agents, merge, add drug gaps and notices. Run its tests with `python -m unittest tests.test_understand -v`.

`dev/mock_agent.py` is a throwaway stand-in for the real brand agent; delete it when that lands.
