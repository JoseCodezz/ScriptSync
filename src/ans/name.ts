/**
 * ANS-style agent name parsing and construction.
 *
 * Format:
 *   a2a://<role>.<category>.<subject>.<version>.<domain>
 * Example:
 *   a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync-demo.com
 *
 * The domain itself can contain dots (e.g. "scriptsync-demo.com"), so we
 * can't just split the whole string on ".". Instead we anchor on the
 * version segment, which always looks like "vMAJOR.MINOR.PATCH", to know
 * exactly where the domain portion starts.
 */

export interface AnsName {
  role: string;
  category: string;
  subject: string;
  version: string;
  domain: string;
  agentSlug: string;
  raw: string;
}

const ANS_PATTERN =
  /^a2a:\/\/([a-zA-Z0-9]+)\.([a-zA-Z0-9]+)\.([a-zA-Z0-9-]+)\.(v\d+\.\d+\.\d+)\.(.+)$/;

/** Parses an ANS-style name. Returns null (never throws) if it doesn't match. */
export function parseAnsName(rawInput: string): AnsName | null {
  const raw = rawInput.trim();
  const match = raw.match(ANS_PATTERN);
  if (!match) return null;

  const [, role, category, subject, version, domain] = match;
  if (!domain.includes(".")) return null; // reject an obviously-not-a-domain tail

  return {
    role,
    category,
    subject,
    version,
    domain: domain.toLowerCase(),
    // Must equal the Python brand agents' `ANSName.dns_label` (= provider/
    // subject only, e.g. "simvastatin") -- that's what they publish in DNS
    // at _agentid.<subject>.<domain> and what they check the "agent" field
    // against in POST /ans/challenge. Anything else here (e.g. including
    // `role`) means this verifier can never match a real agent's DNS record
    // or challenge response.
    agentSlug: subject.toLowerCase(),
    raw,
  };
}

// Builds and ANS signature
export function buildAnsName(params: {
  role: string;
  category: string;
  subject: string;
  version: string;
  domain: string;
}): string {
  const { role, category, subject, version, domain } = params;
  return `a2a://${role}.${category}.${subject}.${version}.${domain}`;
}