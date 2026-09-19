/**
 * GoDaddy's free production Agent Name Service (ANS) -- NOT the classic
 * domain-DNS records API (see ../godaddy/dns.ts).
 *
 * What we have so far ("Part 1"): how to obtain a Production API key from
 * https://classic-developer.godaddy.com/keys. That's a manual, human step
 * (account, mobile-PIN, one-time secret) -- nothing to automate here.
 *
 * What we DON'T have yet ("Part 2"): the actual registration/lookup API --
 * base URL, endpoints, request/response shape, and the auth header format.
 * Every network call below is a deliberate stub that throws rather than
 * guessing at any of that -- making up an endpoint would produce code that
 * looks done but silently fails or, worse, hits the wrong host.
 *
 * Fill in ANS_BASE_URL and each function's request/response handling once
 * that spec is in hand. isGoDaddyAnsConfigured() and the auth header are
 * real and usable now; nothing else is.
 */

export function isGoDaddyAnsConfigured(): boolean {
  return !!(process.env.GODADDY_ANS_API_KEY && process.env.GODADDY_ANS_API_SECRET);
}

function authHeader(): { Authorization: string } {
  const key = process.env.GODADDY_ANS_API_KEY;
  const secret = process.env.GODADDY_ANS_API_SECRET;
  if (!key || !secret) {
    throw new Error(
      "Missing GODADDY_ANS_API_KEY / GODADDY_ANS_API_SECRET environment variables. " +
        "Get a Production key at https://classic-developer.godaddy.com/keys."
    );
  }
  // GoDaddy's other API (domain records) authenticates with
  // `sso-key <key>:<secret>`. UNCONFIRMED whether ANS uses the same scheme --
  // verify against the real docs before relying on this.
  return { Authorization: `sso-key ${key}:${secret}` };
}

function notImplemented(action: string): never {
  throw new Error(
    `${action} is not implemented yet -- the ANS API (base URL, endpoint, request/response ` +
      "shape) hasn't been documented here. Fill in src/ans/godaddyAns.ts once that's available."
  );
}

export interface RegisterWithAnsParams {
  ansName: string; // e.g. "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync.health"
  domain: string;
  publicKeyBase64: string;
}

/**
 * Shape of a successful registration/lookup. SPECULATIVE -- based only on
 * CLAUDE.md's secondhand description of ANS as "domain-anchored identity,
 * version-bound certificates, a transparency log." Adjust field names once
 * a real response is seen; `raw` is kept regardless so a wrong guess above
 * never loses data.
 */
export interface GoDaddyAnsRecord {
  registryId?: string;
  certificateId?: string;
  status?: string;
  raw: unknown;
}

/** STUB. Do not call from a route until this is filled in -- see file header. */
export async function registerWithGoDaddyAns(_params: RegisterWithAnsParams): Promise<GoDaddyAnsRecord> {
  authHeader(); // fails fast with a clear message if the key isn't configured
  notImplemented("registerWithGoDaddyAns()");
}

/** STUB. Backs a future "is this name in GoDaddy's real transparency log" check. */
export async function lookupInGoDaddyAns(_ansName: string): Promise<GoDaddyAnsRecord | null> {
  authHeader();
  notImplemented("lookupInGoDaddyAns()");
}

/** STUB. Backs a future "revoke via GoDaddy's registry" action. */
export async function revokeInGoDaddyAns(_ansName: string): Promise<void> {
  authHeader();
  notImplemented("revokeInGoDaddyAns()");
}
