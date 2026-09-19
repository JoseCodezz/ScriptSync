/**
 * Finds an agents public identity information, doesn't need to search through GoDaddy records
 * Finds publically after GoDaddy post them 
 */
import { promises as dns } from "dns";
import { recordNameFor, parsePublicKeyFromTxtValue } from "./dns";

/**
 * Looks up an agent's public key via standard public DNS resolution.
 * This is what a THIRD-PARTY verifier uses — it does not require any
 * GoDaddy API credentials, because it's just reading public DNS, the same
 * way any resolver on the internet would. GoDaddy is only needed by the
 * domain owner, to publish the record in the first place.
 */
export async function lookupAgentPublicKeyViaDns(
  domain: string,
  agentName: string
): Promise<string | null> {
  const hostname = `${recordNameFor(agentName)}.${domain}`;

  let records: string[][];
  try {
    records = await dns.resolveTxt(hostname);
  } catch (err: any) { // JS uses ENOTFOUND for return error format, Error: NOT FOUND
    if (err.code === "ENOTFOUND" || err.code === "ENODATA") {
      return null;
    }
    throw err;
  }

  // Second attempt to find key, different method
  for (const chunks of records) {
    const value = chunks.join("");
    const key = parsePublicKeyFromTxtValue(value);
    if (key) return key;
  }
  return null;
}