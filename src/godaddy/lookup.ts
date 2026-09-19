/**
 * Finds an agent's public identity information via standard public DNS.
 * Doesn't need GoDaddy credentials -- GoDaddy (or Porkbun, or any registrar)
 * is only needed by the domain owner to publish the record in the first
 * place. A third-party verifier just reads public DNS.
 */
import { promises as dns } from "dns";
import axios from "axios";
import { recordNameFor, parsePublicKeyFromTxtValue } from "./dns";

const DOH_TIMEOUT_MS = 3000;
const DOH_PROVIDERS = ["https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"];

interface DohAnswer {
  type: number;
  data: string;
}
interface DohResponse {
  Status: number;
  Answer?: DohAnswer[];
  Comment?: string | string[];
  extended_dns_errors?: Array<{ extra_text?: string }>;
}

async function resolveTxtPort53(hostname: string): Promise<string[] | null> {
  try {
    const records = await dns.resolveTxt(hostname);
    return records.map((chunks) => chunks.join(""));
  } catch {
    // NXDOMAIN, SERVFAIL, timeout, blocked port 53 -- any of these just
    // means "try DoH next", not "this is an error".
    return null;
  }
}

/**
 * Resolve over DNS-over-HTTPS. Guest/conference networks routinely block
 * outbound port 53 while leaving 443 open, so this is also the fallback
 * that makes DNS work on a locked-down venue network.
 *
 * DNSSEC validation is left ON by default. A resolver SERVFAILing a broken
 * chain of trust is doing its job; only `allowUnvalidated` (opt-in via
 * ANS_ALLOW_UNVALIDATED_DNS) disables it.
 */
async function resolveTxtDoh(
  hostname: string,
  allowUnvalidated = false
): Promise<{ values: string[] | null; diagnosis: string | null }> {
  let diagnosis: string | null = null;

  for (const url of DOH_PROVIDERS) {
    let body: DohResponse;
    try {
      const response = await axios.get<DohResponse>(url, {
        params: { name: hostname, type: "TXT", ...(allowUnvalidated ? { cd: "1" } : {}) },
        headers: { accept: "application/dns-json" },
        timeout: DOH_TIMEOUT_MS,
      });
      body = response.data;
    } catch {
      continue; // try the next provider
    }

    if (body.Status === 2) {
      // SERVFAIL
      const comment = Array.isArray(body.Comment) ? body.Comment.join(" ") : body.Comment ?? "";
      const errors = (body.extended_dns_errors ?? []).map((e) => e.extra_text ?? "").join(" ");
      diagnosis =
        /DNSSEC|DNSKEY|DS /.test(comment) || errors
          ? "SERVFAIL from DNSSEC validation - the registry publishes a DS record but the " +
            "zone serves no matching DNSKEY. Every validating resolver refuses this domain " +
            "until the DS record is removed or the zone is properly signed."
          : "SERVFAIL from the resolver";
      continue;
    }

    const values = (body.Answer ?? [])
      .filter((a) => a.type === 16) // TXT
      .map((a) => a.data.replace(/^"|"$/g, ""));
    if (values.length > 0) return { values, diagnosis: null };
  }

  return { values: null, diagnosis };
}

/**
 * Resolve TXT records, falling back port-53 -> DoH -> DoH (DNSSEC bypassed).
 * Returns `how` so callers can say honestly which path produced the answer
 * (e.g. "DoH (UNVALIDATED - DNSSEC check bypassed)"), matching the same
 * caveat assistant/verify.py already surfaces on the Python side.
 *
 * Setting ANS_ALLOW_UNVALIDATED_DNS=1 exists for one situation: a zone whose
 * chain of trust is broken by a registrar misconfiguration (an orphaned DS
 * record) where the TXT data itself is correct and reachable. It's off by
 * default and never silent.
 */
export async function resolveTxtRecord(hostname: string): Promise<{ values: string[] | null; how: string }> {
  const port53 = await resolveTxtPort53(hostname);
  if (port53 !== null) return { values: port53, how: "port-53" };

  const doh = await resolveTxtDoh(hostname);
  if (doh.values !== null) return { values: doh.values, how: "DoH" };

  if (process.env.ANS_ALLOW_UNVALIDATED_DNS === "1") {
    const unvalidated = await resolveTxtDoh(hostname, true);
    if (unvalidated.values !== null) {
      return { values: unvalidated.values, how: "DoH (UNVALIDATED - DNSSEC check bypassed)" };
    }
  }

  return { values: null, how: doh.diagnosis ?? "port 53 blocked or unreachable, and DoH failed" };
}

/**
 * Looks up an agent's public key via public DNS resolution (port 53, with a
 * DoH fallback). This is what a THIRD-PARTY verifier uses.
 */
export async function lookupAgentPublicKeyViaDns(
  domain: string,
  agentName: string
): Promise<{ key: string | null; how: string }> {
  const hostname = `${recordNameFor(agentName)}.${domain}`;
  const { values, how } = await resolveTxtRecord(hostname);
  if (values === null) return { key: null, how };

  for (const value of values) {
    const key = parsePublicKeyFromTxtValue(value);
    if (key) return { key, how };
  }
  return { key: null, how };
}
