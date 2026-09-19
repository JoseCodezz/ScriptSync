import { Router, Request, Response } from "express";
import { generateChallenge } from "../crypto/agentKeys";
import { buildSigningPayload, verifySignature } from "../crypto/verifyAgent";
import { lookupAgentPublicKeyViaDns } from "../godaddy/lookup";
import {
  saveChallenge,
  getChallenge,
  markChallengeUsed,
  removeExpiredChallenges,
} from "../store";

export const verifyRouter = Router();

const CHALLENGE_TTL_SECONDS = Number(process.env.CHALLENGE_TTL_SECONDS ?? 120);

/**
 * POST /agents/challenge
 * Body: { domain: string, agent: string }
 *
 * The VERIFIER issues a fresh, single-use challenge. The caller must have
 * the agent sign it (via buildSigningPayload) and submit the signature to
 * /agents/verify before it expires. This prevents replay of old signatures.
 */
verifyRouter.post("/challenge", (req: Request, res: Response) => {
  removeExpiredChallenges();
  const { domain, agent } = req.body ?? {};

  if (typeof domain !== "string" || typeof agent !== "string" || !domain || !agent) {
    return res.status(400).json({ error: "domain and agent are required" });
  }

  const issuedAt = Date.now();
  const challenge = generateChallenge();

  saveChallenge({
    domain,
    agentName: agent,
    challenge,
    issuedAt,
    expiresAt: issuedAt + CHALLENGE_TTL_SECONDS * 1000,
    used: false,
  });

  return res.status(200).json({
    domain,
    agent,
    challenge,
    issuedAt,
    expiresInSeconds: CHALLENGE_TTL_SECONDS,
    // The agent must sign exactly this string with its private key.
    payloadToSign: buildSigningPayload({ domain, agent, challenge, issuedAt }),
  });
});

/**
 * POST /agents/verify
 * Body: { domain: string, agent: string, challenge: string, issuedAt: number, signature: string }
 *
 * Verifies: (1) the challenge is known, unused, and unexpired,
 *           (2) the domain currently publishes a matching public key in DNS,
 *           (3) the signature over the exact challenge payload is valid.
 */
verifyRouter.post("/verify", async (req: Request, res: Response) => {
  const { domain, agent, challenge, issuedAt, signature } = req.body ?? {};

  if (
    typeof domain !== "string" ||
    typeof agent !== "string" ||
    typeof challenge !== "string" ||
    typeof issuedAt !== "number" ||
    typeof signature !== "string"
  ) {
    return res.status(400).json({
      error: "domain, agent, challenge, issuedAt (number), and signature are required",
    });
  }

  const pending = getChallenge(challenge);
  if (!pending || pending.domain !== domain || pending.agentName !== agent) {
    return res.status(400).json({ verified: false, reason: "unknown or mismatched challenge" });
  }
  if (pending.used) {
    return res.status(400).json({ verified: false, reason: "challenge already used" });
  }
  if (Date.now() > pending.expiresAt) {
    return res.status(400).json({ verified: false, reason: "challenge expired" });
  }
  if (pending.issuedAt !== issuedAt) {
    return res.status(400).json({ verified: false, reason: "issuedAt does not match issued challenge" });
  }

  let publicKeyBase64: string | null;
  try {
    publicKeyBase64 = await lookupAgentPublicKeyViaDns(domain, agent);
  } catch (err: any) {
    return res.status(502).json({ verified: false, reason: "DNS lookup failed", detail: err.message });
  }

  if (!publicKeyBase64) {
    return res.status(404).json({
      verified: false,
      reason: `No agent-identity TXT record found for ${agent} on ${domain}`,
    });
  }

  const payload = buildSigningPayload({ domain, agent, challenge, issuedAt });
  const validSignature = verifySignature(payload, signature, publicKeyBase64);

  // Single-use regardless of outcome, so a leaked signature can't be retried.
  markChallengeUsed(challenge);

  if (!validSignature) {
    return res.status(401).json({ verified: false, reason: "signature verification failed" });
  }

  return res.status(200).json({
    verified: true,
    domain,
    agent,
    checks: {
      dnsRecordFound: true,
      signatureValid: true,
    },
  });
});