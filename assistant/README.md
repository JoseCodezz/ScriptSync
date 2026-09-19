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
- **Tags** (label authors must use these spellings): CYP3A, interaction, dosing, indication, monitoring, liver, renal, pregnancy, older-adults, pediatric.
- **Discovery**: `config/agents.json`. Add an entry, no code change.

`dev/mock_agent.py` is a throwaway stand-in for the real brand agent; delete it when that lands.
