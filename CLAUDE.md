# ScriptSync - project context for Claude Code

VTHacks 14 hackathon project (Virginia Tech, Goodwin Hall). Team of 4. The original build document calls it "Attest"; the name is now **ScriptSync**.
Tracks: Impiricus (build the next HCP engagement tool; NOT SMS) and GoDaddy (best use of ANS, Agent Name Service). Optional third: Peraton.
**Submission deadline: 8:00 AM Sunday Sept 20, 2026 on Devpost. Judging 9:00 AM, New Classroom Building.** Everyone must be present at closing ceremonies.
Overnight building access is not allowed (out of the buildings by 10:30 PM).

## What we are building
A doctor asks a drug question. Each manufacturer has a small agent that answers only from its own approved label and signs the answer. The doctor's assistant finds those agents, verifies each agent's identity (ANS-style: domain record, certificate/key, public log, not revoked/current), and accepts content only from verified agents. It then:
1. Compares across brands: per-source statements, a "Possible overlap" note when two sources share a topic tag, and "Not covered" when nobody has a passage on a topic asked about.
2. Blocks impostors before content is shown, with a plain-English reason (lookalike domain, expired identity, replayed old message, revoked version). Blocked text is hidden behind a "show what it tried to say" toggle.
3. Lets the doctor control who may contact them (allowed brands, quiet hours; verified-only always on) and keeps an audit log / compliance report.

## Hard rules
- Not medical advice. Never recommend a drug, dose, or treatment. Overlap notes are "cross-references for clinician review".
- Answers are VERBATIM quotes from public FDA label text (DailyMed / openFDA) with section, label version and date. No paraphrasing, no invented claims.
- Agents are OURS, labeled e.g. "demo agent serving the public DailyMed label for X, not operated by the manufacturer". Never impersonate a real company. Attackers claim to be our demo agents.
- No patient data / PHI anywhere.
- Be honest about what is live vs cached vs simulated (ANS verification is currently SIMULATED; signing uses a demo HMAC key). Say so in the demo and on Devpost.
- Never commit the ANS API key/secret; keep them in `.env` (gitignored).

## Team / branches (each person edits mainly their own files)
| Branch | Owner area | Notes |
| --- | --- | --- |
| `assistant-core` | Doctor assistant (Python/FastAPI) | Built. `assistant/`, `common/`, `config/`, `dev/mock_agent.py` |
| `ans-verify` | Verification (Node/TypeScript/Express) | Scaffolding merged to `main` via PR #1. Only `src/crypto/keys.ts` (Ed25519 keys + challenge) has code; the other `src/*.ts` files are empty. Design: public key in a GoDaddy DNS TXT record, agent signs a challenge. |
| `labels-brand-agent` | AI/brand agents + label data | Not started when this was written |
| `frontend` | Web UI, plain HTML/CSS/JS in `web/` | Not started when this was written |
Merge to the shared branch often; pull before starting a task; do not change a contract without telling the group.

## Architecture and contracts
- Assistant: `uvicorn assistant.server:app --port 8080` (run from repo root, venv active). Endpoints: `POST /ask`, `POST /attack/{lookalike|expired|replay|revoked}`, `GET /agents`, `GET/PUT /rules`, `GET /log`, `POST /handoff`, `GET /health`. Interactive API page at `/docs`.
- Ports: assistant 8080; brand agents 9001, 9002; attackers 9101-9104; verification service planned on 8081.
- Discovery: `config/agents.json` (add an entry = new agent, no code change). Rules: `config/rules.json`.
- Agent response (`POST <endpoint>/answer`): `{agentName, brand, answers:[{section, labelVersion, text, tags, title?}], refused, reason?, timestamp, signature}`.
- Signing: `common/signing.py` (HMAC-SHA256, demo key `SCRIPTSYNC_DEMO_KEY`). The assistant rejects bad signatures and any message older than 24 hours.
- Verification: `assistant/verify.py::is_agent_verified(name)` is the ONLY ANS touchpoint. Returns `{name, ok, mode, checks:[{id: dns|cert|log|current, pass, message}], warnings}`. Currently reads `identity_stub` in `config/agents.json`. Plan: call the Node service `POST http://127.0.0.1:8081/verify {"name": ...}` returning the same shape, FAIL CLOSED if it is down (never silently fall back to "verified"); keep an explicitly labeled "simulated" mode.
- Tag spellings (label authors must use these; gap detection depends on them): CYP3A, interaction, dosing, indication, monitoring, liver, renal, pregnancy, older-adults, pediatric.
- `/ask` response: `{question, sources[], refused[], blocked[], unreachable[], skipped[], overlaps[], gaps[], disclaimer}`.
- Overlap/gap logic is deterministic (tags), not an LLM: `assistant/merge.py`.
- `dev/mock_agent.py` + `scripts/start_mocks.ps1` are throwaway stand-ins with placeholder text; delete when the real brand agent lands.

## Running locally (Windows / PowerShell)
```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\scripts\start_mocks.ps1
uvicorn assistant.server:app --port 8080 --reload
```
The Node verification service needs Node + `npm install`; start with `npm run dev`.

## Current status / open decisions
- Done: assistant core (verify stub, merge, signing, audit log, four attacks) - logic tested with stand-ins; not yet run on a real FastAPI install.
- Open: final drug pair (proposal: two real drugs with a shared CYP3A interaction, e.g. simvastatin + clarithromycin; confirm both public labels have interactions sections). Real labels are long; hand-pick 6-8 sections per label into JSON with tags.
- Open: wire `is_agent_verified` to the Node service once the verification teammate confirms the contract.
- Not built yet: web UI (six views: Ask, Verify, Answer, Block, Rules, Receipt), real brand agents, rules inbox, compliance report, live/cached ANS.
- Priorities: Must = ask, discovery, verification (mock OK), brand answers from labels, cross-brand overlap, impostor blocking. Should = gaps + handoff, rules/inbox, audit report, signatures, live/cached ANS. Freeze code Sunday 6 AM; backup demo video; Devpost draft by 7:15 AM.
