import { Router, Request, Response } from "express";
import { parseAnsName, buildAnsName } from "../ans/name";
import { recordNameFor } from "../godaddy/dns";
import { saveAgent } from "../store";
import { appendLogEntry, fingerprintPublicKey } from "../ans/log";

export const registerExternalRouter = Router();

const IDENTITY_TTL_SECONDS = Number(process.env.IDENTITY_TTL_SECONDS ?? 60 * 60 * 24);

/**
 * POST /agents/register-external
 * Body: { ans: string, publicKeyBase64: string }
 *
 * For domains NOT managed through the GoDaddy API (e.g. registered on
 * Porkbun, Namecheap, etc). This does NOT call GoDaddy at all — it assumes
 * you already have your own key pair and will publish the TXT record
 * yourself, manually, through your registrar's dashboard. It just records
 * the registration locally so /verify's "cert", "log", and "current"
 * checks have something to check against.
 *
 * publicKeyBase64 must be the SAME encoding /verify expects: a base64
 * string of the DER SPKI-encoded Ed25519 public key -- exactly what
 * generateAgentKeyPair() in crypto/agentKeys.ts produces as
 * `publicKeyBase64`. If your key came from a different tool, it likely
 * needs re-encoding first -- check with whoever's driving this before
 * trusting the result.
 */
registerExternalRouter.post("/register-external", (req: Request, res: Response) => {
  const { ans, publicKeyBase64 } = req.body ?? {};

  if (typeof ans !== "string" || !ans.trim()) {
    return res.status(400).json({
      error: "ans is required",
      expectedFormat: "a2a://role.category.subject.vMAJOR.MINOR.PATCH.domain",
    });
  }
  if (typeof publicKeyBase64 !== "string" || !publicKeyBase64.trim()) {
    return res.status(400).json({ error: "publicKeyBase64 is required" });
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

  const now = Date.now();
  const expiresAt = now + IDENTITY_TTL_SECONDS * 1000;

  saveAgent({
    ansName: parsed.raw,
    domain: parsed.domain,
    agentSlug: parsed.agentSlug,
    version: parsed.version,
    publicKeyBase64,
    createdAt: now,
    expiresAt,
    revoked: false,
  });

  appendLogEntry({
    ansName: parsed.raw,
    domain: parsed.domain,
    agentSlug: parsed.agentSlug,
    publicKeyFingerprint: fingerprintPublicKey(publicKeyBase64),
    event: "registered",
    at: now,
  });

  const host = `${recordNameFor(parsed.agentSlug)}.${parsed.domain}`;
  return res.status(201).json({
    ansName: parsed.raw,
    recordedLocally: true,
    expiresAt,
    // This is what YOU must go create manually in your registrar's DNS panel.
    dnsRecordToCreateManually: {
      host,
      // Some registrars (Porkbun included) want just the subdomain part,
      // not the full host -- if "host" doesn't work as the "Name" field,
      // try just the part before your domain, e.g. "_agentid.labelagent-simvastatin".
      type: "TXT",
      value: `v=agentkey1; k=${publicKeyBase64}`,
      ttl: "600 (or your registrar's minimum)",
    },
    warning:
      "Nothing was published to DNS by this call. Create the TXT record above yourself in your registrar's dashboard, then wait for propagation before calling POST /verify.",
  });
});