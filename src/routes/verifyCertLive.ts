import { Router, Request, Response } from "express";
import axios from "axios";
import { generateChallenge } from "../crypto/agentKeys";
import { buildSigningPayload, verifySignature } from "../crypto/verifyAgent";

export const verifyCertLiveRouter = Router();

/**
 * POST /agents/verify-live-cert
 * Body: { domain, agent, endpoint, publicKeyBase64 }
 *
 * Stateless proof-of-possession check: issues a fresh challenge, sends it
 * DIRECTLY to the agent's own POST <endpoint>/ans/challenge (not through the
 * /agents/challenge + /agents/verify two-step, which expects some other
 * caller to relay the proof back itself), and verifies the returned
 * signature against publicKeyBase64 -- the key the CALLER already resolved
 * from DNS, never a key the agent volunteers in its own response.
 *
 * This is what assistant/verify.py's "cert" check calls instead of doing the
 * challenge itself in Python. DNS resolution stays in Python (_live_dns
 * already carries the DoH + DNSSEC-bypass workaround scriptsync.health's
 * zone currently needs -- see CLAUDE.md's DNSSEC caveat); duplicating that
 * here would just be two copies of the same workaround to keep in sync.
 * Only the Ed25519 challenge/response step moves to this service.
 */
verifyCertLiveRouter.post("/agents/verify-live-cert", async (req: Request, res: Response) => {
  const { domain, agent, endpoint, publicKeyBase64 } = req.body ?? {};

  if (
    typeof domain !== "string" || !domain ||
    typeof agent !== "string" || !agent ||
    typeof endpoint !== "string" || !endpoint ||
    typeof publicKeyBase64 !== "string" || !publicKeyBase64
  ) {
    return res.status(400).json({
      pass: false,
      message: "domain, agent, endpoint, and publicKeyBase64 are all required",
    });
  }

  const issuedAt = Date.now();
  const challenge = generateChallenge();

  let proof: { publicKey?: string; signature?: string };
  try {
    const response = await axios.post(
      `${endpoint.replace(/\/+$/, "")}/ans/challenge`,
      { domain, agent, challenge, issuedAt },
      { timeout: 5000 }
    );
    proof = response.data ?? {};
  } catch (err: any) {
    // Unreachable or refusing agent is a failure, never a pass.
    return res.status(200).json({
      pass: false,
      message: `Agent could not prove key possession (${err.code ?? err.message}).`,
    });
  }

  // Never trust a key the agent hands back in its own response -- only the
  // one the caller resolved from DNS.
  if (proof.publicKey !== publicKeyBase64) {
    return res.status(200).json({
      pass: false,
      message: "Agent presented a key the domain does not publish.",
    });
  }

  const payload = buildSigningPayload({ domain, agent, challenge, issuedAt });
  const validSignature = verifySignature(payload, proof.signature ?? "", publicKeyBase64);

  return res.status(200).json({
    pass: validSignature,
    message: validSignature
      ? "Signed a fresh challenge with the key published in DNS."
      : "Signature did not verify against the key published in DNS.",
  });
});
