/**
 * Registers the real, already-deployed brand agents (the Python side) into
 * THIS service's local registry, using their EXISTING private keys under
 * keys/<drug>.ed25519 (PEM PKCS8, written by brand_agent.ans.AgentIdentity
 * on that agent's first run).
 *
 * This does NOT generate a new key pair and does NOT touch DNS -- the
 * domain is on Porkbun, not GoDaddy, so DNS is published by hand (see
 * `scripts/ans_records.py`, which prints the exact TXT record). It only
 * tells this verifier's local log/registry about a registration that
 * already exists, so the "log" and "current" checks in POST /verify have
 * something real to check against instead of coming back "not found".
 *
 * Run with the server already up (npm run dev), then:
 *   npx ts-node scripts/register-real-agents.ts
 */
import { createPrivateKey, createPublicKey, KeyObject } from "crypto";
import { existsSync, readFileSync } from "fs";
import { join } from "path";

const ROOT = join(__dirname, "..");
const PORT = process.env.PORT ?? "8081";
const BASE_URL = `http://localhost:${PORT}`;

interface ConfigAgent {
  role: string;
  drug?: string;
  ansName?: string;
}

function publicKeyBase64FromPrivatePem(pem: string): string {
  const privateKey: KeyObject = createPrivateKey({ key: pem, format: "pem", type: "pkcs8" });
  const publicKey = createPublicKey(privateKey);
  return (publicKey.export({ format: "der", type: "spki" }) as Buffer).toString("base64");
}

async function main() {
  const configPath = join(ROOT, "config", "agents.json");
  if (!existsSync(configPath)) {
    console.error(`Missing ${configPath} -- run this from the ScriptSync repo root.`);
    process.exitCode = 1;
    return;
  }

  const config = JSON.parse(readFileSync(configPath, "utf8")) as { agents: ConfigAgent[] };
  const brandAgents = config.agents.filter(
    (a): a is Required<ConfigAgent> => a.role === "brand" && !!a.drug && !!a.ansName
  );

  if (brandAgents.length === 0) {
    console.log("No brand agents found in config/agents.json.");
    return;
  }

  for (const agent of brandAgents) {
    const keyPath = join(ROOT, "keys", `${agent.drug}.ed25519`);
    if (!existsSync(keyPath)) {
      console.log(
        `SKIP  ${agent.drug}: no key at keys/${agent.drug}.ed25519 -- start that brand agent ` +
          "once (it generates its key on first run), then re-run this script."
      );
      continue;
    }

    const publicKeyBase64 = publicKeyBase64FromPrivatePem(readFileSync(keyPath, "utf8"));

    try {
      const res = await fetch(`${BASE_URL}/agents/register-external`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ans: agent.ansName, publicKeyBase64 }),
      });
      const body = await res.json();
      if (res.ok) {
        console.log(`OK    ${agent.drug}: registered ${agent.ansName}`);
      } else {
        console.log(`FAIL  ${agent.drug}: HTTP ${res.status} ${JSON.stringify(body)}`);
      }
    } catch (err: any) {
      console.log(
        `FAIL  ${agent.drug}: could not reach ${BASE_URL} (${err.message}). ` +
          "Is the ans-verify server running (npm run dev)?"
      );
    }
  }

  console.log(
    "\nThis only updates the local log/registry. Confirm the DNS TXT record itself is\n" +
      "actually published at Porkbun -- run `python scripts/ans_records.py` to print the\n" +
      "exact host/value to paste into Porkbun's DNS panel, then `dig +short TXT\n" +
      "_agentid.<drug>.scriptsync.health` to confirm it resolves."
  );
}

main().catch((err) => {
  console.error("register-real-agents crashed:", err);
  process.exitCode = 1;
});
