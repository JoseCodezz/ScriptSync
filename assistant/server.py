"""HCP Assistant server. Run from the repo root:

    uvicorn assistant.server:app --port 8080 --reload
"""
import asyncio
import json
import os
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent

# Load .env BEFORE anything below reads the environment (common.signing reads its key at import).
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:  # python-dotenv is in requirements.txt; without it .env is silently ignored
    print("WARNING: python-dotenv is not installed, so .env was NOT loaded. Run: pip install -r requirements.txt")

from assistant import log as auditlog  # noqa: E402
from assistant.merge import merge_results
from assistant.readable import add_reading_aids
from assistant.understand import analyze, build_notices, drug_gaps, find_phi
from assistant.verify import describe_verification, is_agent_verified
from common import agents_config, ans_registry
from common.signing import SIGNING_MODE, check_freshness, verify_signature

# Brand agents call a model to choose label sections, so responses take a few
# seconds - the old 5s ceiling was written for the instant mock agents and made
# real agents drop out intermittently as "unreachable".
AGENT_TIMEOUT = float(os.environ.get("SCRIPTSYNC_AGENT_TIMEOUT", "45"))

# Presenter-only features (impostor tests) exist only when the assistant is started with
# SCRIPTSYNC_DEMO=1. The product API never exposes them.
DEMO_MODE = os.environ.get("SCRIPTSYNC_DEMO") == "1"

app = FastAPI(title="ScriptSync HCP Assistant")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- config (re-read every request so config edits need no restart) ----------
def load_agents() -> list[dict]:
    return agents_config.load_agents()   # applies SCRIPTSYNC_ENDPOINT_<DRUG> overrides


def load_rules() -> dict:
    with open(ROOT / "config" / "rules.json", encoding="utf-8") as f:
        return json.load(f)


# ---------- talking to one agent ----------
async def call_agent(agent: dict, question: str) -> dict:
    async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
        r = await client.post(agent["endpoint"].rstrip("/") + "/answer", json={"question": question})
        r.raise_for_status()
        return r.json()


def _base(agent: dict) -> dict:
    return {"agent": agent["id"], "brand": agent["brand"], "drug": agent.get("drug"),
            "ansName": agent["ansName"]}


def _blocked(agent, verification, reason, failed_ids, hidden, event):
    auditlog.log_event(event, agent["ansName"], "blocked", reason, hidden)
    return {**_base(agent), "status": "blocked", "reason": reason,
            "failedChecks": failed_ids, "verification": verification,
            # Never shown by default; the UI reveals it only behind the
            # 'show what it tried to say' toggle.
            "hiddenContent": hidden}


async def query_agent(agent: dict, question: str) -> dict:
    """Verify first, then ask, then check signature + freshness (Section 7.2)."""
    # Verification does blocking network work (DNS, a challenge to the agent). Run it in a
    # worker thread so it cannot freeze the server, and so agents are checked in parallel.
    v = await asyncio.to_thread(is_agent_verified, agent["ansName"], agent["endpoint"])
    failed = [c for c in v["checks"] if not c["pass"]]
    auditlog.log_event("verify", agent["ansName"], "pass" if v["ok"] else "fail",
                       "all four checks passed" if v["ok"] else "; ".join(c["message"] for c in failed), v)

    if not v["ok"]:
        hidden = None
        try:  # only so judges can see what the impostor tried to say
            hidden = (await call_agent(agent, question)).get("answers")
        except Exception:
            pass
        reason = "Identity check failed: " + "; ".join(c["message"] for c in failed)
        return _blocked(agent, v, reason, [c["id"] for c in failed], hidden, "verify_failed")

    try:
        resp = await call_agent(agent, question)
    except Exception as e:
        auditlog.log_event("agent_error", agent["ansName"], "unreachable", type(e).__name__)
        return {**_base(agent), "status": "unreachable", "verification": v,
                "reason": f"Agent did not respond ({type(e).__name__})"}

    hidden = resp.get("answers")
    if resp.get("agentName") != agent["ansName"]:
        return _blocked(agent, v, "Response was signed as a different agent name than the one verified.",
                        ["identity"], hidden, "identity_mismatch")
    if not verify_signature(resp):
        return _blocked(agent, v, "Signature check failed: the message was altered or not signed by this agent's key.",
                        ["signature"], hidden, "signature_failed")
    fresh, age_h = check_freshness(resp)
    if not fresh:
        age = f"{age_h / 24:.0f} days old" if age_h is not None else "missing a valid timestamp"
        return _blocked(agent, v, f"Freshness check failed: message is {age} (limit 24 hours). Possible replay.",
                        ["freshness"], hidden, "freshness_failed")

    sig = {"valid": True, "fresh": True, "ageHours": age_h, "mode": SIGNING_MODE}
    if resp.get("refused"):
        auditlog.log_event("refused", agent["ansName"], "refused", resp.get("reason", ""), resp)
        return {**_base(agent), "status": "refused", "verification": v, "signature": sig,
                "reason": resp.get("reason", "Outside this agent's approved label.")}

    auditlog.log_event("answer", agent["ansName"], "verified",
                       f"{len(resp.get('answers', []))} passage(s)", resp.get("answers"))
    return {**_base(agent), "status": "verified", "verification": v, "signature": sig,
            "timestamp": resp.get("timestamp"), "answers": resp.get("answers", [])}


# ---------- API ----------
class AskRequest(BaseModel):
    question: str = ""


@app.get("/health")
def health():
    detail = describe_verification()
    return {"ok": True, "service": "scriptsync-assistant", "verification": detail["mode"],
            "verificationDetail": detail, "signing": SIGNING_MODE, "demo": DEMO_MODE}


def reject_phi(text: str) -> None:
    """Hard rule: no patient data. Refuse before anything is logged or sent to an agent.
    Only the kinds of identifier found are logged, never the text."""
    flags = find_phi(text)
    if flags:
        auditlog.log_event("phi_blocked", "doctor", "blocked", "possible patient identifiers: " + ", ".join(flags))
        raise HTTPException(status_code=400, detail=(
            "This looks like it contains patient identifiers (" + ", ".join(flags) + "). "
            "ScriptSync does not accept patient information. Remove it and ask again."))


@app.post("/ask")
async def ask(req: AskRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please type a question.")
    reject_phi(question)
    agents = load_agents()
    analysis = analyze(question, agents)
    # The question text itself is never stored (it could hold something sensitive):
    # only its length and a short fingerprint.
    auditlog.log_event("ask", "doctor", "received", f"question received ({len(question)} characters; text not stored)", question)
    auditlog.log_event("analyze", "assistant", "ok",
                       f"drugs recognized: {len(analysis['drugsMentioned'])}, "
                       f"drugs without a source: {len(analysis['drugsWithoutAgent'])}, "
                       f"topics: {', '.join(analysis['topics']) or 'none'}, "
                       f"advice-seeking wording: {'yes' if analysis['adviceSeeking'] else 'no'}")

    rules = load_rules()

    async def handle(agent: dict) -> dict:
        if agent["brand"] not in rules.get("allowedBrands", []):
            auditlog.log_event("skipped", agent["ansName"], "skipped", "brand not allowed by doctor's rules")
            return {**_base(agent), "status": "skipped", "reason": "Not on the doctor's allowed list."}
        return await query_agent(agent, question)

    # attackers are only triggered via /attack/{type}; every other agent is checked in parallel
    results = list(await asyncio.gather(*[handle(a) for a in agents if a.get("role") != "attacker"]))

    merged = merge_results(question, results)
    merged["gaps"].extend(drug_gaps(analysis))
    merged["analysis"] = analysis
    add_reading_aids(merged, analysis)   # display-only offsets; the passage text is not touched
    merged["notices"] = build_notices(analysis)
    return merged


DEFAULT_ATTACK_QUESTION = "Is Drug A safe to co-prescribe?"


@app.post("/attack/{attack_type}")
async def attack(attack_type: str, body: dict = Body(default={})):
    if not DEMO_MODE:
        raise HTTPException(403, "Impostor tests are off. Start the assistant with SCRIPTSYNC_DEMO=1 to enable them.")
    agent = next((a for a in load_agents()
                  if a.get("role") == "attacker" and a.get("attack") == attack_type), None)
    if agent is None:
        raise HTTPException(404, f"No attack '{attack_type}'. Options: "
                            + ", ".join(a["attack"] for a in load_agents() if a.get("role") == "attacker"))
    question = (body.get("question") or DEFAULT_ATTACK_QUESTION).strip()
    reject_phi(question)
    auditlog.log_event("attack", agent["ansName"], "started", attack_type)
    return await query_agent(agent, question)


def _visible_agents() -> list[dict]:
    """Impostors are demo-only and must not appear in the product."""
    return [a for a in load_agents() if DEMO_MODE or a.get("role") != "attacker"]


async def _registry_for(agent: dict):
    """The agent's public GoDaddy ANS registration, only if the team linked one. Informational; never affects trust."""
    if not agent.get("ansHost"):
        return None
    return await asyncio.to_thread(ans_registry.lookup, agent["ansHost"])


@app.get("/agents")
async def agents():
    visible = _visible_agents()
    verdicts, registries = await asyncio.gather(
        asyncio.gather(*[asyncio.to_thread(is_agent_verified, a["ansName"], a["endpoint"]) for a in visible]),
        asyncio.gather(*[_registry_for(a) for a in visible]),
    )
    return [{**_base(a), "role": a.get("role", "brand"), "endpoint": a["endpoint"], "verification": v,
             "ansRegistry": reg}
            for a, v, reg in zip(visible, verdicts, registries)]


@app.on_event("startup")
async def prewarm_verification():
    """Resolve DNS and challenge every agent now, so the first question is not the one that pays for it."""
    async def warm():
        await asyncio.gather(*[asyncio.to_thread(is_agent_verified, a["ansName"], a["endpoint"])
                               for a in _visible_agents()],
                             *[_registry_for(a) for a in _visible_agents()], return_exceptions=True)
    asyncio.create_task(warm())


@app.get("/rules")
def get_rules():
    return load_rules()


@app.put("/rules")
def put_rules(rules: dict):
    with open(ROOT / "config" / "rules.json", "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2)
    auditlog.log_event("rules_changed", "doctor", "saved", json.dumps(rules)[:120], rules)
    return rules


@app.get("/log")
def get_log():
    return auditlog.get_events()


class HandoffRequest(BaseModel):
    question: str
    topics: list[str] = []
    brands: list[str] = []


@app.post("/handoff")
def handoff(req: HandoffRequest):
    """Draft only. Sending is display-only in this build."""
    reject_phi(req.question)
    who =", ".join(req.brands) or "the manufacturers"
    what = ", ".join(req.topics) or "the topic in question"
    draft = (f"To the medical information team(s) at {who}:\n\n"
             f"A clinician asked: \"{req.question}\"\n"
             f"The approved labels do not address: {what}.\n"
             "Please provide any approved medical information available on this topic.\n\n"
             "(Draft only - not sent. No patient information is included.)")
    auditlog.log_event("handoff_draft", "doctor", "drafted", what)
    return {"draft": draft}


# ---------- the web UI ----------
# Serving the page from the assistant puts the whole product on one URL (no CORS, no mixed-content
# trouble when hosted). Mounted last so every API route above wins. SCRIPTSYNC_SERVE_WEB=0 turns it off.
WEB_DIR = ROOT / "web"
if WEB_DIR.is_dir() and os.environ.get("SCRIPTSYNC_SERVE_WEB", "1") != "0":
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
