import { Router, Request, Response } from "express";
import { generateAgentKeyPair } from "../crypto/agentKeys";
import { publishAgentTxtRecord, recordNameFor } from "../godaddy/dns";
import { saveAgent } from "../store";

export const registerRouter = Router();

/**
 * POST /agents/register
 * Body: { domain: string, agentName: string }
 *
 * Generates a new Ed25519 key pair, publishes the public key to GoDaddy DNS
 * as a TXT record, and returns the private key to the caller EXACTLY ONCE( EXTREMELY IMPORTANT ).
 * The server never stores the private key*****
 */
registerRouter.post("/register", async (req: Request, res: Response) => {
  const { domain, agentName } = req.body ?? {};

  if (typeof domain !== "string" || !domain.trim()) {
    return res.status(400).json({ error: "domain is required" });
  }
  if (typeof agentName !== "string" || !/^[a-zA-Z0-9-]{1,63}$/.test(agentName)) {
    return res.status(400).json({
      error: "agentName is required and must be alphanumeric/hyphen, 1-63 chars",
    });
  }

  try {
    const keyPair = generateAgentKeyPair();

    await publishAgentTxtRecord(domain, agentName, keyPair.publicKeyBase64);

    saveAgent({
      domain,
      agentName,
      publicKeyBase64: keyPair.publicKeyBase64,
      createdAt: Date.now(),
    });

    return res.status(201).json({
      domain,
      agentName,
      dnsRecord: {
        host: `${recordNameFor(agentName)}.${domain}`,
        type: "TXT",
        value: `v=agentkey1; k=${keyPair.publicKeyBase64}`,
      },
      publicKeyBase64: keyPair.publicKeyBase64,
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