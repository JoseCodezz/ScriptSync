# ScriptSync - project context for Claude Code

VTHacks 14 hackathon project (Virginia Tech, Goodwin Hall). Team of 4. The original build document calls it "Attest"; the name is now **ScriptSync** (UI: "ScriptSync Pulse").
Tracks: Impiricus (build the next HCP engagement tool; NOT SMS) and GoDaddy (best use of ANS, Agent Name Service). Optional third: Peraton.
**Submission deadline: 8:00 AM Sunday Sept 20, 2026 on Devpost. Judging 9:00 AM, New Classroom Building.** Everyone must be present at closing ceremonies. Overnight building access is not allowed (out by 10:30 PM). Code freeze Sunday 6:00 AM; Devpost draft by 7:15 AM.

## What we are building
A doctor asks a drug question in a chat. Each drug has a small label agent that answers only from its own FDA label and signs the answer. The doctor's assistant finds those agents, verifies each one's identity, and accepts content only from verified agents. It then:
1. Shows each source's verbatim passages side by side, a "Possible overlap" note when two sources share a topic tag, and "Not covered" when nobody has a passage on a topic asked about (or when a named drug has no agent).
2. Blocks impostors before content is shown, with a plain-English reason (lookalike domain, expired identity, replayed old message, revoked version). Blocked text is hidden behind a "show what it tried to say" toggle.
3. Lets the doctor control who may contact them (allowed brands enforced; quiet hours stored, not enforced yet; verified-only always on) and keeps an audit log with a JSON export.

## Hard rules
- Not medical advice. Never recommend a drug, dose, or treatment. Overlap notes are "cross-references for clinician review". Advice-seeking questions get a notice, still answered from labels.
- Answers are VERBATIM quotes from public FDA label text (DailyMed / openFDA) with section, label version and date. No paraphrasing, no invented claims. The UI may set a label's own headings apart but never changes a character of a quote (`tests/ui_smoke.py` checks this).
- Agents are OURS: "demo agent serving the public DailyMed label, not operated by the manufacturer". Never impersonate a real company. Attackers claim to be our demo agents.
- Real drugs, generic names, no company branding anywhere. Do NOT invent fictional drugs or label text.
- No patient data / PHI anywhere. The assistant refuses text that looks like patient identifiers and never stores question text.
- Be honest about live vs cached vs simulated (see "Verification: what is real"). Say so in the demo and on Devpost. Never let the UI or /health claim more than is true.
- Never commit secrets: `.env`, `keys/` (agent private keys), API keys. Both are gitignored.

## Where the project stands (Sat Sept 19, night)
| Area | State |
| --- | --- |
| Doctor assistant (`assistant/`) | Done: ask flow, question understanding, PHI guard, overlap/gap logic, rules, handoff drafts, audit log, parallel verification. |
| Brand agents (`brand_agent/`, `labels/`) | Done: simvastatin + clarithromycin, 19 verbatim passages each (side effects, warnings, pregnancy, pediatric, renal/hepatic, overdose, mechanism as well as interactions/dosing; all re-checked against live openFDA), signed answers, Ed25519 identity. Claude picks sections only if `ANTHROPIC_API_KEY` is set; otherwise keyword matching (`selectionMode` says which). A question outside the 19 passages is refused, not stretched. |
| Identity verification | **Partly live**: DNS key lookup + signed challenge are real; registry log + revocation are simulated. The DNSSEC problem is fixed. The remaining blocker is keys on the demo laptop (see "Keys"). |
| Web UI (`web/`) | Done: chat, Sources/Rules/Activity panels, presenter demo guide, readable answers. The Sources panel also shows a linked agent's public GoDaddy ANS registration. |
| Deploy | Done: `python scripts/serve.py` runs everything on one URL; endpoints, ANS hosts and agent keys are set from env vars; an agent can run as its own service. **Not yet:** the public Railway services (`agent.` / `agentb.scriptsync.health`) still run the old placeholder mocks, not the label agents. |
| CI | GitHub Actions on every PR: conflict markers, committed keys, compile, all unit tests, brand-agent checks. Check it is green before merging. |
| GoDaddy ANS | Two agents are registered (see "GoDaddy ANS" below), but under placeholder names, and our verifier does not consult the registry for a verdict. |
| Node `ans-verify` | Parked: merged, but not wired into the assistant, and it names DNS records differently from the Python side. |
| Secrets | `certs/` (ANS certificate keys) is no longer tracked, but those private keys are still in git history: purge and rotate before the repo is public or shared. |
| Not done | Quiet-hours enforcement and a rules inbox; compliance report beyond the JSON export; cached DNS fallback; pitch, Devpost draft, backup video. |

## Verification: what is real
`assistant/verify.py::is_agent_verified(name, endpoint)` is the only identity touchpoint. Result shape: `{name, ok, mode, checks:[{id: dns|cert|log|current, pass, message}], warnings}`. It fails closed (unreachable = fail, never "verified").
- `dns` (LIVE): resolves `_agentid.<agent>.scriptsync.health` (TXT `v=agentkey1; k=<base64 SPKI key>`).
- `cert` (LIVE): challenges the agent to sign a fresh nonce; the signature must verify against the key from DNS, never a key the agent sends.
- `log`, `current` (SIMULATED, labelled "(simulated)"): there is no public registration log or revocation registry yet. Scripted impostor failures come from `identity_stub` in `config/agents.json`, also labelled.
- Signing of answers is HMAC-SHA256 with a shared demo key (`common/signing.py`, `SCRIPTSYNC_DEMO_KEY`). Answers older than 24 hours are rejected.

**DNSSEC (fixed).** The orphaned DS record at the `.health` registry has been cleared: the `_agentid` records now resolve on validating resolvers (checked on 8.8.8.8 and 1.1.1.1). `ANS_ALLOW_UNVALIDATED_DNS=1` is no longer needed; remove it from `.env` so results stop saying "[DNSSEC check bypassed]".

**Keys (demo-day blocker if ignored).** Each agent keeps its private key in `keys/<name>.ed25519` (gitignored) and generates a NEW one on first run. The keys published in DNS came from the machine that ran the agents first, so **any other machine fails the `cert` check** ("Agent presented a key the domain does not publish") and every real agent shows Blocked. To demo from a machine: copy the `keys/` folder from the key holder (USB or similar, never git/chat), or re-publish the records for that machine's keys (`scripts/ans_records.py` prints them, then edit DNS at Porkbun). Check with `start_all.ps1`, which prints each agent's verdict.

**GoDaddy ANS: registered, but not yet a verdict.** GoDaddy's Agent Name Service is a public registry (domain-anchored identity, certificates, a transparency log). Two agents are registered in it: hosts `agent.scriptsync.health` and `agentb.scriptsync.health`, with `_ans` and `_ans-badge` TXT records published; each has a public entry at `transparency.ans.godaddy.com`. Three honest caveats:
- The entries are named `scriptsync-brand-a` / `scriptsync-brand-b` (placeholders), and the services at those hosts still run the old mocks. Do not claim "ANS-verified simvastatin / clarithromycin agents" until the entries are updated or re-registered under the drug names.
- Our verifier does not ask the registry for a verdict. Identity is our own DNS-TXT + signed-challenge scheme (`_agentid.<drug>.scriptsync.health`); `log`/`current` stay simulated.
- The UI shows an agent's public registration only when the team links it: set `SCRIPTSYNC_ANS_HOST_<DRUG>` (for example `SCRIPTSYNC_ANS_HOST_SIMVASTATIN=agent.scriptsync.health`). It is read live by `common/ans_registry.py`, informational only, and shows the registry's own name for the entry. Unlinked agents show nothing, so the page never implies a registration.

## Data and naming decisions (decided)
- Real drugs, real label text, quoted verbatim. Fictional drugs were rejected (invented text is an invented medical claim). The risk is impersonating a company, not naming a drug: generic drug names only, no manufacturer names, brands or logos in the UI, agent names, config or slides.
- Drug pair: **simvastatin + clarithromycin**. Each label independently names the other as a contraindication (simvastatin §4; clarithromycin §4.5), so two agents that never talk converge on the same finding, and a judge can check both on DailyMed.
- Agent naming: by drug + text source. UI: "Simvastatin label agent". ANS-style name: `a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync.health`. Domain `scriptsync.health` is ours, registered at Porkbun.
- "Not covered" means "outside the hand-picked passages these agents serve" (19 sections per label), NOT "the FDA label is silent". Say so in the pitch; the UI does.
- Each question is answered on its own (no chat memory), so a follow-up like "and for kidneys?" does not know the drug. The composer says so.

## Architecture
```
browser (web/, :5500) -> assistant (:8080) -> label agents (:9001 simvastatin, :9002 clarithromycin) -> labels/*.json
                              \-> impostors (:9101-9104, demo mode only, dev/mock_agent.py)
```
- `assistant/`: `server.py` (API), `verify.py` (identity), `understand.py` (question analysis, PHI guard), `merge.py` (overlaps/gaps), `log.py` (audit log, `logs/audit.jsonl`).
- `brand_agent/`: one agent per label (`service.py`), section selection (`selector.py`), ANS identity + DNS (`ans.py`). `labels/*.json` are built from openFDA by `scripts/build_label.py`.
- `common/`: `signing.py` (HMAC signing shared by agents and assistant), `agents_config.py` (the one reader of `config/agents.json`; env overrides for an agent's endpoint and ANS host), `keys.py` (agent private keys from env vars), `ans_registry.py` (an agent's public GoDaddy ANS entry; informational). `config/agents.json` is discovery (add an entry = new agent, no code change); `config/rules.json` is the doctor's rules.
- `web/`: plain HTML/CSS/JS, no build step, calls the assistant at `http://127.0.0.1:8080` (CORS is open). All API text is escaped before it reaches the page.
- Contracts live next to the code: `assistant/README.md` (assistant API, `/ask`, `/health`, tags) and `brand_agent/README.md` (agent wire format, identity). Do not change a contract without telling the group.
- Tag vocabulary (label authors must use these; gap detection depends on them): CYP3A, interaction, dosing, indication, monitoring, liver, renal, pregnancy, older-adults, pediatric, switching, adverse-reactions, warnings, overdose, mechanism.
- Demo mode: `SCRIPTSYNC_DEMO=1` enables `/attack/*` and lists impostors in `/agents`. Without it the assistant is the product API (`/attack` returns 403).
- Settings (`.env`, copy `.env.example`): `ANTHROPIC_API_KEY` (optional; leave it unset unless it is a real key), `ANS_DOMAIN`, `ANS_DNS_VERIFY`, `SCRIPTSYNC_DEMO`, `SCRIPTSYNC_DEMO_KEY`, and for hosting `ANS_KEY_<DRUG>_PEM_B64`, `SCRIPTSYNC_ENDPOINT_<DRUG>`, `SCRIPTSYNC_ANS_HOST_<DRUG>` (see "Deploying").

## Running locally
Windows / PowerShell:
```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env         # then delete the ANTHROPIC_API_KEY placeholder line unless you have a real key
.\scripts\start_all.ps1 -Restart   # agents, impostors, assistant, web UI (add -Product to hide impostors)
.\scripts\stop_all.ps1
```
If scripts are blocked: `powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1 -Restart`. It starts everything in about 15 s and prints each real agent's identity verdict. Open http://localhost:5500 (add `?demo=1` for the presenter guide). Logs are in `logs/run/`.
Mac/Linux/WSL: `scripts/run_brand_agents.sh`, then `scripts/preflight.sh` (both assume `.venv/bin/python`; set `PYTHON` for a Windows venv).

Tests (none need the network unless noted):
- `python -m unittest discover -s tests -t . -p "test_*.py" -v` (every unit test; CI runs this on each PR)
- `python -m tests.test_brand_agent` (`VERIFY_VERBATIM=1` re-checks every quote against live openFDA)
- `python tests/ui_smoke.py`: optional browser test of the whole UI against the live stack (`pip install playwright`; needs demo mode and agents that pass verification).

Gotchas:
- **Stale processes** cause most odd results (old agents still bound to a port, signing with something else). Use `-Restart`.
- On Windows the venv `python.exe` is a launcher; the process owning a port is its child.
- Live verification takes a few seconds the first time (DNS over DoH); results are cached 120 s (15 s if failed) and pre-warmed at startup.

## Deploying the website (one command)
`python scripts/serve.py` runs everything in one process tree on one URL: the label agents (localhost only), the assistant, and the web UI (the assistant serves `web/` itself, so the page and API share an origin: no CORS, no mixed-content trouble).
- **Local:** `python scripts/serve.py` -> http://127.0.0.1:8080. `SCRIPTSYNC_DEMO=1` also starts the impostors and the presenter tools. `--dry-run` prints what would start; `--no-agents` runs only the assistant + UI (agents hosted elsewhere).
- **Hosting platform (Render / Railway / Fly / Heroku-style):** Build `pip install -r requirements.txt`, Start `python scripts/serve.py` (a `Procfile` and `.python-version` are included). The platform sets `PORT`; the script then binds `0.0.0.0`.
- **Keys:** the agents' private keys are not in git. On a host, add each as a secret env var `ANS_KEY_<DRUG>_PEM_B64` (base64 of `keys/<drug>.ed25519`; PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("keys\simvastatin.ed25519"))`). Without them the agent generates a fresh identity that DNS does not publish and the identity checks fail. The host also needs outbound internet (DNS over HTTPS for the identity check).
- **Web page hosted separately** (static host): publish the `web/` folder and set `window.SCRIPTSYNC_API` in `web/config.js` to the assistant's public HTTPS URL. Serving the page from the assistant is simpler and preferred.
- `SCRIPTSYNC_SERVE_WEB=0` stops the assistant serving the page (API only).
- **A label agent as its own service** (e.g. a Railway service per agent): start `python -m brand_agent --label labels/<drug>.json --host 0.0.0.0 --port $PORT --domain scriptsync.health`. It writes its key from `ANS_KEY_<DRUG>_PEM_B64` before loading its identity, so keep DNS's `_agentid.<drug>.scriptsync.health` key and this secret the same key.
- **The site pointing at public agents:** set `SCRIPTSYNC_ENDPOINT_<DRUG>=https://<public agent url>` (for example `SCRIPTSYNC_ENDPOINT_SIMVASTATIN`). The assistant, identity verification and `serve.py` all read `config/agents.json` through `common/agents_config.py`, so they agree on the URL, and `serve.py` skips starting an agent that is hosted elsewhere. Keep each agent's `ansName` unchanged: the identity check reads DNS by name, not by URL.

## Demo and pitch plan
Story: a doctor can't tell whether a message claiming to be from a manufacturer is real. About 3 minutes.
1. **Problem (~20s):** drug information comes from many sources with no easy way to tell which are genuine. Impiricus angle: a next-generation HCP engagement tool that is not SMS.
2. **Idea (~20s):** each drug's label agent answers only from its own approved label and signs the answer. The doctor's assistant accepts content only from agents whose identity checks out.
3. **Live demo (~90s), using the presenter guide (`?demo=1`, needs `start_all.ps1` demo mode):** interaction question (two verified answers + overlap); a gap (pregnancy: "Not covered"); switching (advice notice + gaps); an impostor arriving inside a normal answer, blocked before any text shows; Rules (turn a brand off, ask again: skipped); Activity and export; patient details refused; what's live vs simulated.
4. **Say what is real, before anyone asks (~20s):** two of four identity checks are live (DNS key + signed challenge); registry log and revocation are simulated; answer signing uses a demo key; label text is a cached snapshot of the current FDA label; "Not covered" means outside our curated passages. Same wording on Devpost.
5. **Close (~20s):** in production each manufacturer runs its own agent under its own domain; the doctor's side needs no changes.
- GoDaddy angle: identity comes from a domain record, a key and a signed challenge, and impostors stop at that step. Two agents are registered in GoDaddy's ANS and their public log entries are one click away in the Sources panel (once linked). Say "ANS-style verification, with ANS-registered agents": our verifier does not consult the registry, and the registered names are placeholders until updated.
- Never say the tool recommends anything. Do not call the agents "AI agents" unless `ANTHROPIC_API_KEY` is actually on (check `/health` on an agent: `selection`).
- Before demoing: run `start_all.ps1` and confirm both agents say verified. Record a backup video in case the live demo fails.

## Team workflow
- `main` is the trunk and what judges see. Work on a branch and open a PR into `main`; no direct pushes (a bad push breaks everyone's demo). CI runs on every PR: wait for it to be green before merging. `develop` is stale (it stops before the deploy and answer-quality work): do not merge into it.
- Areas (from the commit history): assistant + web UI (Kyle), brand agents + ANS in Python (Jose), Node `ans-verify` (Miles).
- Pull `main` before starting; keep changes in your own area; tell the group about contract changes (the tag vocabulary, `/ask`, `/health`, the agent wire format).

## Open items (in priority order)
1. **Demo laptop:** get Jose's `keys/` folder onto it (USB, never git or chat), run `.\scripts\start_all.ps1 -Restart`, and confirm both agents say verified. Remove `ANS_ALLOW_UNVALIDATED_DNS` from `.env`. Run `python tests/ui_smoke.py` as the check.
2. **Railway:** redeploy the two public services as the real label agents (start command under "Deploying", keys as secrets), and optionally a third service for the site (`python scripts/serve.py`).
3. **ANS registry:** update or re-register the two entries under the drug names (they are `scriptsync-brand-a/b`), then link them with `SCRIPTSYNC_ANS_HOST_<DRUG>`. Optionally have the verifier use the registry for `log`/`current`.
4. **Secrets:** purge the `certs/` private keys from git history and rotate the ANS certificates before the repo is public or shared with judges; then delete the `:!certs` exclusion from `.github/workflows/tests.yml`.
5. Cached DNS fallback (labelled "cached") so a blocked venue network does not turn every real agent to Blocked.
6. Decide the rules inbox / quiet-hours enforcement; compliance report beyond the JSON export.
7. Tidy: retire `develop`; rebuild the impostors on the real agent (their hidden text still says "Drug A"); one source of truth for the tag list (copied in several files); move the ANS client from `brand_agent/` to `common/`.
8. Pitch, Devpost draft, backup video, rehearsal on venue-like Wi-Fi.

## Known limitations
Answers come from 19 hand-picked passages per label, chosen by keyword matching unless a Claude key is set, so a question outside them is honestly refused ("Not covered"); each question is answered on its own; CORS is open on the assistant (fine on a laptop, not for deployment); the audit log is in memory plus `logs/audit.jsonl`; the Node service is not wired in.
