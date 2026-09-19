import { Router, Request, Response } from "express";
import { generateAgentKeyPair } from "../crypto/agentKeys";
import { publishAgentTxtRecord, recordNameFor } from "../godaddy/dns";
import { parseAnsName, buildAnsName } from "../ans/name";
import { saveAgent } from "../store";
import { appendLogEntry, fingerprintPublicKey } from "../ans/log";

export const registerRouter = Router();

const IDENTITY_TTL_SECONDS = Number(process.env.IDENTITY_TTL_SECONDS ?? 60 * 60 * 24); // 24h default

/**
 * POST /agents/register
 * Body: { ans: string }
 *   ans = full ANS-style name, e.g.
 *   "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync-demo.com"
 * 
 * Generates a new Ed25519 key pair, publishes the public key to GoDaddy DNS
 * as a TXT record under that name's domain, records the registration in the
 * (simulated) public log, and returns the private key to the caller EXACTLY
 * ONCE. The server never stores the private key.
 */
registerRouter.post("/register", async (req: Request, res: Response) => {
  const { ans } = req.body ?? {};

  if (typeof ans !== "string" || !ans.trim()) {
    return res.status(400).json({
      error: "ans is required",
      expectedFormat: "a2a://role.category.subject.vMAJOR.MINOR.PATCH.domain",
    });
  }

  const parsed = parseAnsName(ans);
  if (!parsed) {
    return res.status(400).json({
      error: "ans is not a valid ANS-style name",
      expectedFormat: "a2a://role.category.subject.vMAJOR.MINOR.PATCH.domain",
      example: buildAnsName({
        role: "labelAgent",
        category: "drugInfo",
        subject: "simvastatin",
        version: "v1.0.0",
        domain: "your-team-domain.com",
      }),
    });
  }

  try {
    const keyPair = generateAgentKeyPair();

    await publishAgentTxtRecord(parsed.domain, parsed.agentSlug, keyPair.publicKeyBase64);

    const now = Date.now();
    const expiresAt = now + IDENTITY_TTL_SECONDS * 1000;

    saveAgent({
      ansName: parsed.raw,
      domain: parsed.domain,
      agentSlug: parsed.agentSlug,
      version: parsed.version,
      publicKeyBase64: keyPair.publicKeyBase64,
      createdAt: now,
      expiresAt,
      revoked: false,
    });

    appendLogEntry({
      ansName: parsed.raw,
      domain: parsed.domain,
      agentSlug: parsed.agentSlug,
      publicKeyFingerprint: fingerprintPublicKey(keyPair.publicKeyBase64),
      event: "registered",
      at: now,
    });

    return res.status(201).json({
      ansName: parsed.raw,
      dnsRecord: {
        host: `${recordNameFor(parsed.agentSlug)}.${parsed.domain}`,
        type: "TXT",
        value: `v=agentkey1; k=${keyPair.publicKeyBase64}`,
      },
      publicKeyBase64: keyPair.publicKeyBase64,
      expiresAt,
      // Shown ONCE. The agent must store this securely — it is not retrievable again.
      privateKeyBase64: keyPair.privateKeyBase64,
      warning:
        "Store privateKeyBase64 securely now. It will not be shown again and is not stored server-side.",
    });
  } catch (err: any) {
    return res.status(502).json({
      error: "Failed to publish DNS record via GoDaddy",
      detail: err?.response?.data ?? err.message,
    });
  }
});