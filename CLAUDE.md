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
- Real drugs, generic names, no company branding anywhere (see "Data and naming decisions"). Do NOT invent fictional drugs or label text: invented text is an invented medical claim.
- No patient data / PHI anywhere.
- Be honest about what is live vs cached vs simulated (ANS verification is currently SIMULATED; signing uses a demo HMAC key). Say so in the demo and on Devpost.
- Never commit the ANS API key/secret; keep them in `.env` (gitignored).

## Data and naming decisions (decided)
- **Real drugs, real label text.** Public FDA label text (DailyMed / openFDA), quoted verbatim. Fictional drugs were considered and rejected: invented label text breaks "no invented claims" and weakens the pitch ("verbatim from the real FDA label, with section and version" is the credibility point; the CYP3A overlap is real pharmacology judges can check).
- **The risk is impersonating a company, not using a drug name.** Public label text is public. So: generic drug names only; no manufacturer names, brand names or logos in the UI, agent names, config, or slides.
- **Agent naming:** name by drug + text source, never by company.
  - UI display name: `Simvastatin label agent` with the subtitle "demo agent serving the public DailyMed label, not operated by the manufacturer".
  - ANS-style name: `a2a://labelAgent.drugInfo.simvastatin.v1.0.0.<our-domain>`.
  - `<our-domain>` MUST be a domain the team owns (GoDaddy). Never a manufacturer's domain. The verification design puts the agent's public key in a DNS TXT record on that domain, so we need to control it anyway, and it makes the GoDaddy/ANS story real. Until then `.example` names are placeholders.
- **Drug pair (proposed, not yet confirmed):** simvastatin + clarithromycin, chosen for a shared CYP3A interaction. Before `labels-brand-agent` starts, someone must read BOTH real labels and confirm each has an interactions section that supports the CYP3A story. If not, pick another pair. Real labels are long: hand-pick 6-8 sections per label into JSON with the tags below.
- The current "Brand A / Brand B / Drug A / Drug B" names and `[PLACEHOLDER]` text (in `config/agents.json` and `dev/mock_agent.py`) are throwaway and must all be replaced.

## Team / branches (each person edits mainly their own files)
| Branch | Owner area | Notes |
| --- | --- | --- |
| `assistant-core` | Doctor assistant (Python/FastAPI) | Built. `assistant/`, `common/`, `config/`, `dev/mock_agent.py` |
| `ans-verify` | Verification (Node/TypeScript/Express) | Scaffolding merged to `main` via PR #1. Only `src/crypto/keys.ts` (Ed25519 keys + challenge) has code; the other `src/*.ts` files are empty. Design: public key in a GoDaddy DNS TXT record, agent signs a challenge. |
| `labels-brand-agent` | AI/brand agents + label data | Not started when this was written |
| `frontend` | Web UI, plain HTML/CSS/JS in `web/` | First page built (`web/index.html`, `style.css`, `app.js`): Ask, Answer (quotes, overlaps, gaps), impostor attack buttons with hidden-text toggle. Merged `develop` in to get the assistant. Verify, Rules, Receipt views not built yet. Owner works on it alone. |
Merge to the shared branch often; pull before starting a task; do not change a contract without telling the group.

## Architecture and contracts
- Assistant: `uvicorn assistant.server:app --port 8080` (run from repo root, venv active). Endpoints: `POST /ask`, `POST /attack/{lookalike|expired|replay|revoked}`, `GET /agents`, `GET/PUT /rules`, `GET /log`, `POST /handoff`, `GET /health`. Interactive API page at `/docs`.
- Ports: assistant 8080; brand agents 9001, 9002; attackers 9101-9104; verification service planned on 8081; web UI static server 5500.
- Web UI (`web/`): plain HTML/CSS/JS, no build step, calls the assistant at `http://127.0.0.1:8080` (CORS is open). All API text is inserted with `textContent`, never `innerHTML`. It renders whatever the API returns, so swapping mock data for real labels needs no UI change. Blocked text stays hidden behind the "show what it tried to say" toggle.
- Discovery: `config/agents.json` (add an entry = new agent, no code change). Rules: `config/rules.json`.
- Agent response (`POST <endpoint>/answer`): `{agentName, brand, answers:[{section, labelVersion, text, tags, title?}], refused, reason?, timestamp, signature}`.
- Signing: `common/signing.py` (HMAC-SHA256, demo key `SCRIPTSYNC_DEMO_KEY`). The assistant rejects bad signatures and any message older than 24 hours.
- Verification: `assistant/verify.py::is_agent_verified(name)` is the ONLY ANS touchpoint. Returns `{name, ok, mode, checks:[{id: dns|cert|log|current, pass, message}], warnings}`. Currently reads `identity_stub` in `config/agents.json`. Plan: call the Node service `POST http://127.0.0.1:8081/verify {"name": ...}` returning the same shape, FAIL CLOSED if it is down (never silently fall back to "verified"); keep an explicitly labeled "simulated" mode.
- Tag spellings (label authors must use these; gap detection depends on them): CYP3A, interaction, dosing, indication, monitoring, liver, renal, pregnancy, older-adults, pediatric, switching. (`switching` is NEW: a passage that actually addresses changing from one drug to another. Most labels have none, so a switching question usually ends in "Not covered". Tell whoever writes label JSON.)
- `/ask` response: `{question, sources[], refused[], blocked[], unreachable[], skipped[], overlaps[], gaps[], notices[], analysis, disclaimer}`. `notices[]` = `{type, message}` (e.g. `advice`: the question sounds like it wants a recommendation, so we warn and reframe but still answer). `analysis` = `{drugsMentioned[], drugsWithoutAgent[], topics[], switching, adviceSeeking}`. `gaps[]` can now include `{topic: "drug: <name>"}` for a drug named in a switching question that has no verified source. The web UI does not render `notices` yet (it ignores unknown fields).
- Question understanding lives in `assistant/understand.py` (deterministic, no LLM). Drug names are read from each brand agent's `drug` + optional `aliases` in `config/agents.json`.
- PHI guard: `/ask`, `/attack/*`, `/handoff` return HTTP 400 if the text looks like patient identifiers; nothing is sent to agents; the audit log stores question length + fingerprint only, never the text. `logs/` is gitignored; entries written before this change may still contain test questions.
- Overlap/gap logic is deterministic (tags), not an LLM: `assistant/merge.py`.
- `dev/mock_agent.py` + `scripts/start_mocks.ps1` are throwaway stand-ins with placeholder text; delete when the real brand agent lands.

## Demo and pitch plan
Story: a doctor can't tell whether a message claiming to be from a manufacturer is real. About 3 minutes.
1. **Problem (~20s):** drug information comes from many sources with no easy way to tell which are genuine. Impiricus angle: a next-generation HCP engagement tool that is not SMS.
2. **Idea (~20s):** each manufacturer runs a small agent that answers only from its own approved label and signs the answer. The doctor's assistant accepts content only from agents whose identity checks out.
3. **Live demo, in this order (~90s):**
   1. Ask the CYP3A question: two verified answers with a "Possible overlap" note.
   2. Ask the kidney question: "Not covered", it does not guess.
   3. Click **Lookalike domain**: blocked before any text shows, with a plain-English reason; then "Show what it tried to say" to reveal the fake claim. Lead with this, it is the memorable moment.
   4. Show **Rules** (who may contact the doctor) and the **Receipt** (audit log).
4. **State what is simulated, before anyone asks (~20s):** ANS verification is simulated/cached and signing uses a demo key. Judges reward honesty and the hard rules require it. Same wording on Devpost.
5. **Close (~20s):** in production each manufacturer runs its own agent under its own domain; the doctor's side needs no changes.
- GoDaddy track: stress that identity comes from a domain record, a key and a public log, and impostors are stopped at that step.
- Never say the tool recommends anything. The overlap note is a "cross-reference for clinician review".
- Record a backup demo video in case the live demo fails.

## Running locally (Windows / PowerShell)
```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\scripts\start_mocks.ps1
uvicorn assistant.server:app --port 8080 --reload
```
Serve the web UI (second terminal, repo root): `python -m http.server 5500 --directory web`, then open http://localhost:5500. (Opening `index.html` directly also works since CORS is open.)
The Node verification service needs Node + `npm install`; start with `npm run dev`.

Gotchas:
- **Stale mocks:** if legitimate agents show as "Blocked: signature check failed", old mock processes from an earlier session are probably still bound to ports 9001-9104 and signing with something the current code rejects (new ones silently fail to bind). Check with `Get-NetTCPConnection -State Listen -LocalPort 9001,9002,9101,9102,9103,9104`, stop the old ones, then run `.\scripts\start_mocks.ps1` again.
- `start_mocks.ps1` launches plain `python`, so the venv must be active or the mocks won't start.
- On Windows the venv `python.exe` is a launcher that spawns the base Python as a child process, so the PID that owns a port is the child, not the one you launched.

## Current status / open decisions
- Done: assistant core (verify stub, merge, signing, audit log, four attacks). Now exercised end to end on a real FastAPI install via the web UI and curl with the mocks: a CYP3A question gives two verified answers plus a CYP3A overlap; a kidney question gives "Not covered: renal"; all four attacks are blocked with the right reason (replay is blocked on freshness).
- Done (first cut): web UI Ask + Answer + impostor attacks (see `frontend` row). Not yet checked in a real browser; only the API calls were tested.
- Done (`assistant-core`): question understanding (drug recognition from agent config, topics, switching, advice-seeking wording), PHI guard, PHI-safe audit logging, drug-level "Not covered", `switching` topic. Unit-tested (`tests/test_understand.py`) and checked end to end against the mocks.
- Decided: real drugs with generic names and verbatim label text, our own domain for ANS names (see "Data and naming decisions").
- Open: confirm the drug pair by reading both real labels (proposal: simvastatin + clarithromycin).
- Open: buy/confirm the domain we own for ANS names and DNS TXT keys (GoDaddy).
- Open: wire `is_agent_verified` to the Node service once the verification teammate confirms the contract.
- Not built yet: web UI views Verify, Rules, Receipt (and the handoff-draft button on gaps); real brand agents with real label JSON; rules inbox; compliance report; live/cached ANS.
- Priorities: Must = ask, discovery, verification (mock OK), brand answers from labels, cross-brand overlap, impostor blocking. Should = gaps + handoff, rules/inbox, audit report, signatures, live/cached ANS. Freeze code Sunday 6 AM; backup demo video; Devpost draft by 7:15 AM.
