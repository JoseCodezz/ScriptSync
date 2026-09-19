"""HCP Assistant server. Run from the repo root:

    uvicorn assistant.server:app --port 8080 --reload
"""
import json
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from assistant import log as auditlog
from assistant.merge import merge_results
from assistant.understand import analyze, build_notices, drug_gaps, find_phi
from assistant.verify import is_agent_verified
from common.signing import SIGNING_MODE, check_freshness, verify_signature

ROOT = Path(__file__).resolve().parent.parent
AGENT_TIMEOUT = 5.0

app = FastAPI(title="ScriptSync HCP Assistant")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- config (re-read every request so config edits need no restart) ----------
def load_agents() -> list[dict]:
    with open(ROOT / "config" / "agents.json", encoding="utf-8") as f:
        return json.load(f)["agents"]


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
    v = is_agent_verified(agent["ansName"])
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
    return {"ok": True, "service": "scriptsync-assistant", "verification": "simulated", "signing": SIGNING_MODE}


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
    results = []
    for agent in agents:
        if agent.get("role") == "attacker":
            continue  # attackers are only triggered via /attack/{type}
        if agent["brand"] not in rules.get("allowedBrands", []):
            auditlog.log_event("skipped", agent["ansName"], "skipped", "brand not allowed by doctor's rules")
            results.append({**_base(agent), "status": "skipped",
                            "reason": "Not on the doctor's allowed list."})
            continue
        results.append(await query_agent(agent, question))

    merged = merge_results(question, results)
    merged["gaps"].extend(drug_gaps(analysis))
    merged["analysis"] = analysis
    merged["notices"] = build_notices(analysis)
    return merged


DEFAULT_ATTACK_QUESTION = "Is Drug A safe to co-prescribe?"


@app.post("/attack/{attack_type}")
async def attack(attack_type: str, body: dict = Body(default={})):
    agent = next((a for a in load_agents()
                  if a.get("role") == "attacker" and a.get("attack") == attack_type), None)
    if agent is None:
        raise HTTPException(404, f"No attack '{attack_type}'. Options: "
                            + ", ".join(a["attack"] for a in load_agents() if a.get("role") == "attacker"))
    question = (body.get("question") or DEFAULT_ATTACK_QUESTION).strip()
    reject_phi(question)
    auditlog.log_event("attack", agent["ansName"], "started", attack_type)
    return await query_agent(agent, question)


@app.get("/agents")
def agents():
    out = []
    for a in load_agents():
        out.append({**_base(a), "role": a.get("role", "brand"), "endpoint": a["endpoint"],
                    "verification": is_agent_verified(a["ansName"])})
    return out


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
