// Parameters for Agent Records, EXTREMELEY IMPORTANT
export interface AgentRecord {
  ansName: string; // full ANS-style name, e.g. "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.example.com"
  domain: string;
  agentSlug: string; // DNS-record label, e.g. "labelagent-simvastatin"
  version: string; // e.g. "v1.0.0", parsed from the ANS name at registration
  publicKeyBase64: string;
  createdAt: number;
  expiresAt: number; // identity must be re-registered/renewed after this
  revoked: boolean;
}

// Parameters for Pending Challenge, EXTREMELY IMPORTANT
export interface PendingChallenge {
  domain: string;
  agentName: string;
  challenge: string;
  issuedAt: number;
  expiresAt: number;
  used: boolean;
}

// Puts all Agents and Pending Challenges into Maps [finds objects by key]
const agentsByAnsName = new Map<string, AgentRecord>();
const challenges = new Map<string, PendingChallenge>();

function agentKey(domain: string, agentName: string): string {
  return `${domain.toLowerCase()}::${agentName.toLowerCase()}`;
}

export function saveAgent(record: AgentRecord): void {
  agentsByAnsName.set(record.ansName, record);
}

/** Look up by the exact ANS-style name — this is what POST /verify receives. */
export function getAgentByAnsName(ansName: string): AgentRecord | undefined {
  return agentsByAnsName.get(ansName);
}

/** Look up by domain + DNS agent slug — used by the interactive challenge flow. */
export function getAgent(domain: string, agentName: string): AgentRecord | undefined {
  const key = agentKey(domain, agentName);
  for (const record of agentsByAnsName.values()) {
    if (agentKey(record.domain, record.agentSlug) === key) return record;
  }
  return undefined;
}

export function revokeAgent(ansName: string): void {
  const record = agentsByAnsName.get(ansName);
  if (record) record.revoked = true;
}

export function saveChallenge(challenge: PendingChallenge): void {
  challenges.set(challenge.challenge, challenge);
}

export function getChallenge(challenge: string): PendingChallenge | undefined {
  return challenges.get(challenge);
}

// Marks challenges used to use a different one
export function markChallengeUsed(challenge: string): void {
  const existing = challenges.get(challenge);
  if (existing) existing.used = true;
}

// Periodically call this (or run on an interval) to avoid unbounded growth
export function removeExpiredChallenges(now: number = Date.now()): void {
  for (const [key, c] of challenges) {
    if (c.expiresAt < now) challenges.delete(key);
  }
}