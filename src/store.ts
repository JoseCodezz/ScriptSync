// Parameters for Agent Records, EXTREMELEY IMPORTANT
export interface AgentRecord {
  domain: string;
  agentName: string;
  publicKeyBase64: string;
  createdAt: number;
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

// Puts all Agents and Pending Challenges into a Map[Finds object through a key]
const agents = new Map<string, AgentRecord>();
const challenges = new Map<string, PendingChallenge>();

// Standard object methods 
function agentKey(domain: string, agentName: string): string {
  return `${domain.toLowerCase()}::${agentName.toLowerCase()}`;
}

export function saveAgent(record: AgentRecord): void {
  agents.set(agentKey(record.domain, record.agentName), record);
}

export function getAgent(domain: string, agentName: string): AgentRecord | undefined {
  return agents.get(agentKey(domain, agentName));
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