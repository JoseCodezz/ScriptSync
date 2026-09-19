/** 
 * Checks if AI Agent holds the private key 
*/

import { sign, verify as cryptoVerify, KeyObject } from "crypto";
import { publicKeyFromBase64 } from "./agentKeys";

/**
 * Signs a message with an Ed25519 private key. This runs on the AGENT side
 * (or is simulated here for testing/demo purposes) — the private key never
 * touches the verifier.
 */
export function signMessage(message: string, privateKey: KeyObject): string {
  const signature = sign(null, Buffer.from(message, "utf8"), privateKey);
  return signature.toString("base64");
}

/**
 * Verifies a signature against a message using the agent's published
 * public key (base64 SPKI, as read from the DNS TXT record).
 */
export function verifySignature(
  message: string,
  signatureBase64: string,
  publicKeyBase64: string
): boolean {
  try {
    const publicKey = publicKeyFromBase64(publicKeyBase64);
    return cryptoVerify(
      null,
      Buffer.from(message, "utf8"),
      publicKey,
      Buffer.from(signatureBase64, "base64")
    );
  } catch {
    // Malformed key or signature -> treat as not verified, never throw.
    return false;
  }
}

/**
 * Builds the exact canonical string the agent must sign for a given
 * challenge round. Both sides (issuer and verifier) must construct this
 * identically, or valid signatures will fail to verify.
 */
export function buildSigningPayload(params: {
  domain: string;
  agent: string;
  challenge: string;
  issuedAt: number;
}): string {
  const { domain, agent, challenge, issuedAt } = params;
  return `agent-identity-v1|${domain}|${agent}|${challenge}|${issuedAt}`;
}