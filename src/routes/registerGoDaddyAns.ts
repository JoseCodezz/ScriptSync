import { Router, Request, Response } from "express";
import { parseAnsName, buildAnsName } from "../ans/name";
import { registerWithGoDaddyAns, isGoDaddyAnsConfigured } from "../ans/godaddyAns";
import { saveAgent } from "../store";
import { appendLogEntry, fingerprintPublicKey } from "../ans/log";

export const registerGoDaddyAnsRouter = Router();

const IDENTITY_TTL_SECONDS = Number(process.env.IDENTITY_TTL_SECONDS ?? 60 * 60 * 24);

/**
 * GET /agents/godaddy-ans/status
 * Cheap way to check "is a key even configured" without trying a real call.
 */
registerGoDaddyAnsRouter.get("/godaddy-ans/status", (_req: Request, res: Response) => {
  return res.status(200).json({
    configured: isGoDaddyAnsConfigured(),
    implemented: false, // flip once src/ans/godaddyAns.ts's stubs are filled in
  });
});

/**
 * POST /agents/register-godaddy-ans
 * Body: { ans: string, publicKeyBase64: string }
 *
 * Registers with GoDaddy's real ANS registry. Until src/ans/godaddyAns.ts
 * is filled in (waiting on the actual API spec), this validates input and
 * returns 501 with a clear reason -- never a crash, never a fake success.
 *
 * On a real success it also records locally, same as register-external, so
 * POST /verify's local log/current checks stay consistent no matter which
 * registration path was used. That does NOT make GoDaddy's ANS the source
 * of truth for /verify -- whether /verify's checks should call GoDaddy's
 * ANS directly (e.g. for "log"/"current") is a separate decision, once we
 * know whether ANS exposes its own lookup API.
 */
registerGoDaddyAnsRouter.post("/register-godaddy-ans", async (req: Request, res: Response) => {
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

  if (!isGoDaddyAnsConfigured()) {
    return res.status(501).json({
      error: "GoDaddy ANS is not configured",
      detail: "Set GODADDY_ANS_API_KEY and GODADDY_ANS_API_SECRET (see .env.example).",
    });
  }

  try {
    const registration = await registerWithGoDaddyAns({
      ansName: parsed.raw,
      domain: parsed.domain,
      publicKeyBase64,
    });

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

    return res.status(201).json({ ansName: parsed.raw, godaddyAns: registration, expiresAt });
  } catch (err: any) {
    return res.status(501).json({ error: "GoDaddy ANS registration not available yet", detail: err.message });
  }
});
