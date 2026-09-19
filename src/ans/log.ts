/**
 * A minimal, append-only "public log" of agent registrations and
 * revocations, standing in for a real transparency log (like Certificate
 * Transparency) for the demo. ITS A SIMULATED LOG
 * 
 * A list in memory of agents that get registered or revoked
 */

import { loadJson, saveJson } from "../persist";

export interface LogEntry {
  ansName: string;
  domain: string;
  agentSlug: string;
  publicKeyFingerprint: string;
  event: "registered" | "revoked";
  at: number;
}

const entries: LogEntry[] = loadJson<LogEntry[]>("log.json", []);

export function appendLogEntry(entry: LogEntry): void {
  entries.push(entry);
  saveJson("log.json", entries);
}

// True if this exact ANS name has a "registered" entry in the log
export function isInLog(ansName: string): boolean {
  return entries.some((e) => e.ansName === ansName && e.event === "registered");
}

export function getLog(): LogEntry[] {
  return entries;
}

// Short, human-checkable fingerprint of a public key, for log entries
export function fingerprintPublicKey(publicKeyBase64: string): string {
  const { createHash } = require("crypto");
  return createHash("sha256").update(publicKeyBase64).digest("hex").slice(0, 16);
}