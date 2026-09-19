/**
 * Lifecycle test for the ANS identity logic — register, verify checks,
 * revoke — using a placeholder domain instead of the team's real one.
 *
 * "test-domain.example" is IANA-reserved for documentation and testing
 * (RFC 2606), so it's guaranteed to never be a real, resolvable domain.
 * That's the point: this script proves the store/log/check logic is
 * correct WITHOUT needing GoDaddy credentials or real DNS, by calling
 * those functions directly instead of going through the network-dependent
 * publishAgentTxtRecord() / lookupAgentPublicKeyViaDns() calls.
 *
 * Run with:  npx ts-node scripts/test-lifecycle.ts
 *
 * What this does NOT test (needs the real domain + credentials):
 *   - publishAgentTxtRecord() actually writing a TXT record via GoDaddy
 *   - lookupAgentPublicKeyViaDns() actually resolving that record publicly
 *   - the "dns" check in POST /verify succeeding for a real registration
 * Once the real domain is live, re-run this same lifecycle through actual
 * HTTP calls to a running server (see the curl commands printed at the end)
 * to confirm those two pieces too.
 */

/// <reference types="node" />
import { generateAgentKeyPair } from "../src/crypto/agentKeys";
import { parseAnsName, buildAnsName } from "../src/ans/name";
import {
  saveAgent,
  getAgentByAnsName,
  revokeAgent,
  AgentRecord,
} from "../src/store";
import { appendLogEntry, fingerprintPublicKey, isInLog } from "../src/ans/log";

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

async function main() {
  const TEST_DOMAIN = "test-domain.example"; // IANA-reserved, never a real site

  console.log("=== 1. Build and parse an ANS name ===");
  const ansRaw = buildAnsName({
    role: "labelAgent",
    category: "drugInfo",
    subject: "simvastatin",
    version: "v1.0.0",
    domain: TEST_DOMAIN,
  });
  console.log(`  ans name: ${ansRaw}`);

  const parsed = parseAnsName(ansRaw);
  check("parses successfully", parsed !== null);
  if (!parsed) return;
  check("role extracted correctly", parsed.role === "labelAgent");
  check("subject extracted correctly", parsed.subject === "simvastatin");
  check("version extracted correctly", parsed.version === "v1.0.0");
  check("domain extracted correctly", parsed.domain === TEST_DOMAIN);
  // agentSlug must equal just the subject/drug name (e.g. "simvastatin"), matching
  // the real brand agents' ANSName.dns_label -- see ans/name.ts for why.
  check("agentSlug built correctly", parsed.agentSlug === "simvastatin");

  console.log("\n=== 2. Reject a malformed name ===");
  const bad = parseAnsName("not-a-real-ans-name");
  check("malformed name returns null, not a throw", bad === null);

  console.log("\n=== 3. Simulate registration (no GoDaddy call) ===");
  const keyPair = generateAgentKeyPair();
  const now = Date.now();
  const IDENTITY_TTL_SECONDS = 60 * 60 * 24; // 24h, matches register.ts default
  const expiresAt = now + IDENTITY_TTL_SECONDS * 1000;

  const record: AgentRecord = {
    ansName: parsed.raw,
    domain: parsed.domain,
    agentSlug: parsed.agentSlug,
    version: parsed.version,
    publicKeyBase64: keyPair.publicKeyBase64,
    createdAt: now,
    expiresAt,
    revoked: false,
  };
  saveAgent(record);
  appendLogEntry({
    ansName: parsed.raw,
    domain: parsed.domain,
    agentSlug: parsed.agentSlug,
    publicKeyFingerprint: fingerprintPublicKey(keyPair.publicKeyBase64),
    event: "registered",
    at: now,
  });

  const fetched = getAgentByAnsName(parsed.raw);
  check("record retrievable by ANS name", fetched !== undefined);
  check("stored public key matches generated key", fetched?.publicKeyBase64 === keyPair.publicKeyBase64);
  check("not revoked at registration", fetched?.revoked === false);
  check("appears in the simulated public log", isInLog(parsed.raw));

  console.log("\n=== 4. Confirm an unregistered name is correctly absent ===");
  const neverRegistered = buildAnsName({
    role: "labelAgent",
    category: "drugInfo",
    subject: "clarithromycin",
    version: "v1.0.0",
    domain: TEST_DOMAIN,
  });
  check("unregistered name has no record", getAgentByAnsName(neverRegistered) === undefined);
  check("unregistered name is not in the log", !isInLog(neverRegistered));

  console.log("\n=== 5. Revoke and confirm it sticks ===");
  revokeAgent(parsed.raw);
  appendLogEntry({
    ansName: parsed.raw,
    domain: parsed.domain,
    agentSlug: parsed.agentSlug,
    publicKeyFingerprint: fingerprintPublicKey(keyPair.publicKeyBase64),
    event: "revoked",
    at: Date.now(),
  });
  const afterRevoke = getAgentByAnsName(parsed.raw);
  check("record now shows revoked: true", afterRevoke?.revoked === true);
  check(
    "this is exactly what flips the 'current' check to fail in POST /verify",
    afterRevoke?.revoked === true
  );

  console.log("\n=== 6. Expiry logic (no waiting required — simulate the clock) ===");
  const expiredRecord: AgentRecord = {
    ...record,
    ansName: buildAnsName({
      role: "labelAgent",
      category: "drugInfo",
      subject: "expiredtestdrug",
      version: "v1.0.0",
      domain: TEST_DOMAIN,
    }),
    expiresAt: Date.now() - 1000, // already in the past
    revoked: false,
  };
  saveAgent(expiredRecord);
  const fetchedExpired = getAgentByAnsName(expiredRecord.ansName);
  check("expired record is retrievable", fetchedExpired !== undefined);
  check("expiresAt is in the past", (fetchedExpired?.expiresAt ?? Infinity) < Date.now());
  check(
    "this is exactly what flips the 'current' check to fail with an expiry message",
    (fetchedExpired?.expiresAt ?? Infinity) < Date.now()
  );

  console.log(`\n=== RESULT: ${passCount} passed, ${failCount} failed ===`);
  if (failCount > 0) process.exitCode = 1;

  console.log(
    "\nOnce the real domain is live, confirm the network-dependent half with:\n" +
      `  curl -X POST http://localhost:8081/agents/register -H "Content-Type: application/json" -d '{"ans":"${ansRaw.replace(
        TEST_DOMAIN,
        "<your-real-domain>"
      )}"}'\n` +
      `  curl -X POST http://localhost:8081/verify -H "Content-Type: application/json" -d '{"name":"${ansRaw.replace(
        TEST_DOMAIN,
        "<your-real-domain>"
      )}"}'\n`
  );
}

main().catch((err) => {
  console.error("Test script crashed:", err);
  process.exitCode = 1;
});