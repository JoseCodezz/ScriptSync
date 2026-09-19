/**
 * End-to-end test for ANS identification: the crypto identity core
 * (sign/verify) and the full HTTP surface (register-external, the public
 * log, POST /verify's four checks, revoke, the interactive challenge flow)
 * against an in-process copy of the real Express app.
 *
 * Runs fully offline and deterministically by default, using a reserved
 * test domain ("test-domain.example", RFC 2606 -- guaranteed to never
 * resolve). That means the live "dns"/"cert" checks are EXPECTED to fail
 * closed here, and the test asserts exactly that: no record published means
 * never verified, even with a cryptographically valid signature. That's the
 * actual security property this system exists to enforce, so it's worth
 * testing on purpose rather than skipping because "there's no network".
 * "log" and "current" run for real, against this service's own registry.
 *
 * Also covers the GoDaddy ANS route (src/routes/registerGoDaddyAns.ts):
 * input validation and the fact that it fails closed with 501 while
 * src/ans/godaddyAns.ts is still a stub -- never a fake success. Once that
 * stub is filled in with the real API call, these assertions should be
 * replaced with ones that exercise the real registration.
 *
 * Set ANS_TEST_LIVE=1 to additionally exercise real DNS resolution (with
 * the DoH + DNSSEC-bypass fallback) against ANS_DOMAIN (default
 * scriptsync.health) for the "simvastatin" agent. That needs network and an
 * actually-published record, so it's opt-in, reported separately, and never
 * affects the offline suite's pass/fail count.
 *
 * Uses a throwaway ANS_DATA_DIR so this never touches the real server's
 * data/ directory. Run with:
 *   npx ts-node scripts/test-ans-identification.ts
 *   ANS_TEST_LIVE=1 npx ts-node scripts/test-ans-identification.ts
 */

import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { AddressInfo } from "net";

// Must happen before importing anything under src/ -- store.ts and
// ans/log.ts read this at first use to decide where to persist.
const TEST_DATA_DIR = mkdtempSync(join(tmpdir(), "ans-verify-test-"));
process.env.ANS_DATA_DIR = TEST_DATA_DIR;

import { createApp } from "../src/app";
import { generateAgentKeyPair } from "../src/crypto/agentKeys";
import { signMessage, verifySignature, buildSigningPayload } from "../src/crypto/verifyAgent";
import { buildAnsName } from "../src/ans/name";
import { lookupAgentPublicKeyViaDns } from "../src/godaddy/lookup";

let passCount = 0;
let failCount = 0;

function check(label: string, condition: boolean): void {
  if (condition) {
    console.log(`  PASS  ${label}`);
    passCount++;
  } else {
    console.log(`  FAIL  ${label}`);
    failCount++;
  }
}

function findCheck(checks: Array<{ id: string; pass: boolean; message: string }>, id: string) {
  const found = checks.find((c) => c.id === id);
  if (!found) throw new Error(`No "${id}" check in response: ${JSON.stringify(checks)}`);
  return found;
}

async function main() {
  const app = createApp();
  const server = app.listen(0);
  await new Promise<void>((resolve) => server.once("listening", resolve));
  const port = (server.address() as AddressInfo).port;
  const BASE = `http://127.0.0.1:${port}`;

  const TEST_DOMAIN = "test-domain.example"; // IANA-reserved (RFC 2606), never resolves

  try {
    console.log("=== 1. Crypto identity core (no network) ===");
    const agentKeys = generateAgentKeyPair();
    const attackerKeys = generateAgentKeyPair();
    const payload = buildSigningPayload({
      domain: TEST_DOMAIN,
      agent: "simvastatin",
      challenge: "test-challenge-abc",
      issuedAt: 1_700_000_000_000,
    });
    const signature = signMessage(payload, agentKeys.privateKey);

    check("valid signature verifies against the signing key", verifySignature(payload, signature, agentKeys.publicKeyBase64));
    check(
      "signature does NOT verify against a different agent's key",
      !verifySignature(payload, signature, attackerKeys.publicKeyBase64)
    );
    check(
      "signature does NOT verify over a tampered payload",
      !verifySignature(payload + "x", signature, agentKeys.publicKeyBase64)
    );
    const tamperedSig = signature.slice(0, -4) + (signature.slice(-4) === "AAAA" ? "BBBB" : "AAAA");
    check(
      "tampered signature does NOT verify",
      !verifySignature(payload, tamperedSig, agentKeys.publicKeyBase64)
    );
    check(
      "buildSigningPayload is deterministic",
      buildSigningPayload({ domain: TEST_DOMAIN, agent: "simvastatin", challenge: "c", issuedAt: 1 }) ===
        buildSigningPayload({ domain: TEST_DOMAIN, agent: "simvastatin", challenge: "c", issuedAt: 1 })
    );

    console.log("\n=== 2. register-external + public log ===");
    const ansName = buildAnsName({
      role: "labelAgent",
      category: "drugInfo",
      subject: "simvastatin",
      version: "v1.0.0",
      domain: TEST_DOMAIN,
    });

    const registerRes = await fetch(`${BASE}/agents/register-external`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ans: ansName, publicKeyBase64: agentKeys.publicKeyBase64 }),
    });
    const registerBody = (await registerRes.json()) as any;
    check("register-external returns 201", registerRes.status === 201);
    check("register-external records locally (no DNS call)", registerBody.recordedLocally === true);
    check(
      "register-external's DNS instructions use the un-prefixed drug name as host",
      registerBody.dnsRecordToCreateManually?.host === `_agentid.simvastatin.${TEST_DOMAIN}`
    );

    const logRes = await fetch(`${BASE}/log`);
    const logBody = (await logRes.json()) as any;
    check(
      "public log contains the registration event",
      logBody.entries.some((e: any) => e.ansName === ansName && e.event === "registered")
    );

    console.log("\n=== 3. POST /verify: log + current are real; dns/cert fail closed (no real DNS) ===");
    const verify1Res = await fetch(`${BASE}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: ansName }),
    });
    const verify1 = (await verify1Res.json()) as any;
    check("overall ok is false without a real DNS record", verify1.ok === false);
    check("log check passes: registration event was found", findCheck(verify1.checks, "log").pass === true);
    check(
      "current check passes: not revoked, not expired, version matches",
      findCheck(verify1.checks, "current").pass === true
    );
    check("dns check fails closed: no real domain to resolve", findCheck(verify1.checks, "dns").pass === false);
    check(
      "cert check fails closed: no DNS-published key to compare against",
      findCheck(verify1.checks, "cert").pass === false
    );

    console.log("\n=== 4. POST /verify for a name that was never registered ===");
    const neverRegistered = buildAnsName({
      role: "labelAgent",
      category: "drugInfo",
      subject: "never-registered-drug",
      version: "v1.0.0",
      domain: TEST_DOMAIN,
    });
    const verify2Res = await fetch(`${BASE}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: neverRegistered }),
    });
    const verify2 = (await verify2Res.json()) as any;
    check("unregistered name: ok is false", verify2.ok === false);
    check("unregistered name: every check fails", verify2.checks.every((c: any) => c.pass === false));

    console.log("\n=== 5. Revoke flips 'current' to fail and is recorded in the log ===");
    const revokeRes = await fetch(`${BASE}/agents/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: ansName }),
    });
    check("revoke returns 200", revokeRes.status === 200);

    const verify3Res = await fetch(`${BASE}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: ansName }),
    });
    const verify3 = (await verify3Res.json()) as any;
    check("current check now fails after revocation", findCheck(verify3.checks, "current").pass === false);
    check(
      "current check message says revoked",
      /revoked/i.test(findCheck(verify3.checks, "current").message)
    );

    const logRes2 = await fetch(`${BASE}/log`);
    const logBody2 = (await logRes2.json()) as any;
    check(
      "public log now has both a registered and a revoked event for this name",
      logBody2.entries.filter((e: any) => e.ansName === ansName).length === 2
    );

    console.log("\n=== 6. Interactive challenge/verify fails closed even with a cryptographically valid signature ===");
    // This is the actual security property: a real agent, signing correctly,
    // still gets rejected if its domain has published nothing in DNS.
    const domain2 = "another-test-domain.example";
    const agent2 = "unpublished-agent";
    const challengeRes = await fetch(`${BASE}/agents/challenge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: domain2, agent: agent2 }),
    });
    const challengeBody = (await challengeRes.json()) as any;
    const proofSignature = signMessage(challengeBody.payloadToSign, agentKeys.privateKey);

    const verifyChallengeRes = await fetch(`${BASE}/agents/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain: domain2,
        agent: agent2,
        challenge: challengeBody.challenge,
        issuedAt: challengeBody.issuedAt,
        signature: proofSignature,
      }),
    });
    const verifyChallengeBody = (await verifyChallengeRes.json()) as any;
    check(
      "unpublished domain: verified is false despite a valid signature",
      verifyChallengeBody.verified === false
    );
    check(
      "reason names the missing DNS record, not a signature failure",
      /No agent-identity TXT record found/i.test(verifyChallengeBody.reason ?? "")
    );

    const replayRes = await fetch(`${BASE}/agents/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain: domain2,
        agent: agent2,
        challenge: challengeBody.challenge,
        issuedAt: challengeBody.issuedAt,
        signature: proofSignature,
      }),
    });
    const replayBody = (await replayRes.json()) as any;
    check("a used challenge cannot be replayed", replayBody.reason === "challenge already used");

    console.log("\n=== 7. GoDaddy ANS route: validation and stub behavior (no real API call yet) ===");
    const statusRes = await fetch(`${BASE}/agents/godaddy-ans/status`);
    const statusBody = (await statusRes.json()) as any;
    check("status endpoint reports not yet implemented", statusBody.implemented === false);
    check(
      "status endpoint reports not configured (no key in this test's env)",
      statusBody.configured === false
    );

    const badAnsRes = await fetch(`${BASE}/agents/register-godaddy-ans`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ans: "not-a-real-ans-name", publicKeyBase64: "abc" }),
    });
    check("malformed ans name is rejected with 400 before touching GoDaddy", badAnsRes.status === 400);

    const missingKeyRes = await fetch(`${BASE}/agents/register-godaddy-ans`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ans: ansName }),
    });
    check("missing publicKeyBase64 is rejected with 400", missingKeyRes.status === 400);

    const unconfiguredRes = await fetch(`${BASE}/agents/register-godaddy-ans`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ans: ansName, publicKeyBase64: agentKeys.publicKeyBase64 }),
    });
    const unconfiguredBody = (await unconfiguredRes.json()) as any;
    check(
      "valid request fails closed with 501 when no GoDaddy ANS key is configured (never a fake success)",
      unconfiguredRes.status === 501 && /not configured/i.test(unconfiguredBody.error ?? "")
    );

    console.log(`\n=== OFFLINE RESULT: ${passCount} passed, ${failCount} failed ===`);

    if (process.env.ANS_TEST_LIVE === "1") {
      console.log("\n=== 8. (opt-in, live) Real DNS lookup with the DoH/DNSSEC-bypass fallback ===");
      const liveDomain = process.env.ANS_DOMAIN ?? "scriptsync.health";
      const { key, how } = await lookupAgentPublicKeyViaDns(liveDomain, "simvastatin");
      console.log(
        key
          ? `  FOUND  public key at _agentid.simvastatin.${liveDomain} (via ${how})`
          : `  NONE   no key found for _agentid.simvastatin.${liveDomain} (${how})`
      );
      console.log("  (informational only -- does not affect the offline pass/fail count above)");
    } else {
      console.log("\nSet ANS_TEST_LIVE=1 to also check real DNS for ANS_DOMAIN (default scriptsync.health).");
    }
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    rmSync(TEST_DATA_DIR, { recursive: true, force: true });
  }

  if (failCount > 0) process.exitCode = 1;
}

main().catch((err) => {
  console.error("test-ans-identification crashed:", err);
  process.exitCode = 1;
});
