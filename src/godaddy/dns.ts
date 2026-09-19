/** 
 * DNS = Domain Name System
 * Changes human-readible domain names to computer-readible names 
 * Uses TXT records from DNS to find AI Agent public key 
*/

// HTTP request library
import axios from "axios";

const GODADDY_API_BASE = process.env.GODADDY_API_BASE || "https://api.godaddy.com";

function authHeader(): { Authorization: string } {
  const key = process.env.GODADDY_API_KEY;
  const secret = process.env.GODADDY_API_SECRET;
  if (!key || !secret) {
    throw new Error(
      "Missing GODADDY_API_KEY / GODADDY_API_SECRET environment variables."
    );
  }
  return { Authorization: `sso-key ${key}:${secret}` };
}

/** The DNS host label used for a given agent, e.g. "_agentid.support-agent". */
export function recordNameFor(agentName: string): string {
  return `_agentid.${agentName}`;
}

/**
 * Publishes (creates or overwrites) a TXT record at
 * `_agentid.<agentName>.<domain>` containing the agent's public key.
 *
 * Requires GoDaddy API credentials with DNS-write permission on `domain`.
 */
export async function publishAgentTxtRecord(
  domain: string,
  agentName: string,
  publicKeyBase64: string
): Promise<void> {
  const recordName = recordNameFor(agentName);
  const value = `v=agentkey1; k=${publicKeyBase64}`;

  await axios.put(
    `${GODADDY_API_BASE}/v1/domains/${encodeURIComponent(domain)}/records/TXT/${encodeURIComponent(
      recordName
    )}`,
    [{ data: value, ttl: 600 }],
    { headers: { ...authHeader(), "Content-Type": "application/json" } }
  );
}

/**
 * Reads the TXT record for an agent from GoDaddy directly (useful right after
 * publishing, before public DNS has propagated). For verification at
 * arbitrary later times, prefer `lookupAgentPublicKeyViaDns`, which queries
 * public DNS resolvers and works for any registrar, not just GoDaddy.
 */
export async function readAgentTxtRecordFromGoDaddy(
  domain: string,
  agentName: string
): Promise<string | null> {
  const recordName = recordNameFor(agentName);
  const res = await axios.get(`${GODADDY_API_BASE}/v1/domains/${encodeURIComponent(domain)}/records/TXT/${encodeURIComponent(recordName)}`, { headers: authHeader() });
  const records = res.data as Array<{ data: string }>;
  return records.length > 0 ? records[0].data : null;
}

/** Extracts the base64 public key from a raw TXT record value like "v=agentkey1; k=<base64>". */
export function parsePublicKeyFromTxtValue(txtValue: string): string | null {
  const match = txtValue.match(/k=([A-Za-z0-9+/=_-]+)/);
  return match ? match[1] : null;
}