import { Router, Request, Response } from "express";
import { parseAnsName } from "../ans/name";
import { lookupAgentPublicKeyViaDns } from "../godaddy/lookup";
import { getAgentByAnsName } from "../store";
import { isInLog } from "../ans/log";

export const contractVerifyRouter = Router();

const MODE: "simulated" | "live" =
  process.env.VERIFICATION_MODE === "live" ? "live" : "simulated";

interface CheckResult {
  id: "dns" | "cert" | "log" | "current";
  pass: boolean;
  message: string;
}

/**
 * POST /verify
 * Body: { name: string }  -- the ANS-style agent name, e.g.
 *   "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync-demo.com"
 *
 * This is the ONLY endpoint assistant/verify.py::is_agent_verified() calls.
 * Response shape matches the contract exactly:
 *   { name, ok, mode, checks: [{id, pass, message}], warnings }
 *
 * FAILS CLOSED: any missing data, parse failure, or lookup error produces
 * ok:false with an explanatory check — never a silent ok:true.
 *
 * Checks:
 *   dns     - is a public key published in DNS for this domain/agent?
 *   cert    - does the DNS-published key match what we recorded at
 *             registration? (stand-in for a real certificate/CA check —
 *             there is no real CA here, see "mode" and the warning below)
 *   log     - is there a registration event for this exact name in the
 *             (simulated) public log?
 *   current - is the identity un-revoked, unexpired, and on the requested
 *             version?
 */
contractVerifyRouter.post("/verify", async (req: Request, res: Response) => {
  const { name } = req.body ?? {};
  const warnings: string[] = [];

  if (MODE === "simulated") {
    warnings.push(
      "ANS verification is running in SIMULATED mode: dns and cert checks are real; " +
        "log and current/revocation checks use an in-memory simulated log on this one " +
        "server, not an independent, cryptographically-verifiable public log."
    );
  }

  if (typeof name !== "string" || !name.trim()) {
    return res.status(400).json({
      name: typeof name === "string" ? name : "",
      ok: false,
      mode: MODE,
      checks: [],
      warnings: [...warnings, "Missing or invalid 'name' field."],
    });
  }

  const parsed = parseAnsName(name);
  const checks: CheckResult[] = [];

  if (!parsed) {
    checks.push({
      id: "dns",
      pass: false,
      message: `"${name}" is not a valid ANS-style name (expected a2a://role.category.subject.vMAJOR.MINOR.PATCH.domain).`,
    });
    return res.status(200).json({ name, ok: false, mode: MODE, checks, warnings });
  }

  const record = getAgentByAnsName(parsed.raw);

  // --- Check 1: dns ---
  let publishedKey: string | null = null;
  try {
    const { key, how } = await lookupAgentPublicKeyViaDns(parsed.domain, parsed.agentSlug);
    publishedKey = key;
    checks.push({
      id: "dns",
      pass: !!publishedKey,
      message: publishedKey
        ? `Public key found in DNS TXT record for ${parsed.agentSlug}.${parsed.domain} (via ${how}).`
        : `No agent-identity TXT record found for ${parsed.agentSlug}.${parsed.domain} (${how}).`,
    });
  } catch (err: any) {
    checks.push({ id: "dns", pass: false, message: `DNS lookup failed: ${err.message}` });
  }

  // --- Check 2: cert (key-consistency stand-in for a real certificate) ---
  if (!record) {
    checks.push({
      id: "cert",
      pass: false,
      message: `No registration on file for "${parsed.raw}" — cannot confirm key ownership.`,
    });
  } else if (!publishedKey) {
    checks.push({
      id: "cert",
      pass: false,
      message: "Cannot confirm key consistency without a DNS-published key.",
    });
  } else if (publishedKey !== record.publicKeyBase64) {
    checks.push({
      id: "cert",
      pass: false,
      message:
        "DNS-published key does not match the key on file from registration (possible tampering or stale record).",
    });
  } else {
    checks.push({
      id: "cert",
      pass: true,
      message: "DNS-published key matches the key recorded at registration.",
    });
  }

  // --- Check 3: log ---
  const inLog = isInLog(parsed.raw);
  checks.push({
    id: "log",
    pass: inLog,
    message: inLog
      ? "Registration event found in the public log."
      : "No registration event found in the public log.",
  });

  // --- Check 4: current (not revoked, not expired, version matches) ---
  if (!record) {
    checks.push({ id: "current", pass: false, message: "No registration on file." });
  } else if (record.revoked) {
    checks.push({ id: "current", pass: false, message: "This identity has been revoked." });
  } else if (Date.now() > record.expiresAt) {
    checks.push({
      id: "current",
      pass: false,
      message: `Identity expired at ${new Date(record.expiresAt).toISOString()}; re-registration required.`,
    });
  } else if (record.version !== parsed.version) {
    checks.push({
      id: "current",
      pass: false,
      message: `Requested version ${parsed.version} does not match registered version ${record.version}.`,
    });
  } else {
    checks.push({ id: "current", pass: true, message: "Identity is current, not revoked, not expired." });
  }

  const ok = checks.every((c) => c.pass);

  return res.status(200).json({ name: parsed.raw, ok, mode: MODE, checks, warnings });
});