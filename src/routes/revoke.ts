import { Router, Request, Response } from "express";
import { parseAnsName } from "../ans/name";
import { getAgentByAnsName, revokeAgent } from "../store";
import { appendLogEntry, fingerprintPublicKey } from "../ans/log";

export const revokeRouter = Router();

/**
 * POST /agents/revoke
 * Body: { name: string }  -- ANS-style name to revoke.
 *
 * Demo/testing tool: marks a registered identity as revoked, so the
 * "current" check in POST /verify starts failing for it. This is what
 * backs the "revoked version" attack scenario in the demo plan.
 *
 * Registers an agent as revoked contract 
 */
revokeRouter.post("/revoke", (req: Request, res: Response) => {
  const { name } = req.body ?? {};
  if (typeof name !== "string" || !name.trim()) {
    return res.status(400).json({ error: "name is required" });
  }

  const parsed = parseAnsName(name);
  if (!parsed) {
    return res.status(400).json({ error: "not a valid ANS-style name" });
  }

  const record = getAgentByAnsName(parsed.raw);
  if (!record) {
    return res.status(404).json({ error: "no registration found for that name" });
  }

  revokeAgent(parsed.raw);
  appendLogEntry({
    ansName: parsed.raw,
    domain: parsed.domain,
    agentSlug: parsed.agentSlug,
    publicKeyFingerprint: fingerprintPublicKey(record.publicKeyBase64),
    event: "revoked",
    at: Date.now(),
  });

  return res.status(200).json({ name: parsed.raw, revoked: true });
});