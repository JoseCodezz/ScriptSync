/** 
 * Provides cryptographic function to give an AI agent a unique identity 
 * Creates an Ed25519 public/private key pair then converts to Base64 for storage and transmission
 * Generates random challenges than an AI can sign to prove its private key
 */

import { 
  generateKeyPairSync, KeyObject, } from "crypto";

export interface Ed25519KeyPair {
  publicKey: KeyObject;
  privateKey: KeyObject;
  publicKeyBase64: string;
  privateKeyBase64: string; // PKCS8 DER, base64-encoded. Handed to the agent ONCE
}

/**
 * Generates a fresh Ed25519 key pair for an agent. 
 * Ed25519 is a modern public-key crytographic algrorithm
 * The public key is what gets published in dns.ts
 * The private key is returned once to the caller and never stored server-side
 */
export function generateAgentKeyPair(): Ed25519KeyPair {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");

  const publicKeyDer = publicKey.export({ format: "der", type: "spki" });
  const privateKeyDer = privateKey.export({ format: "der", type: "pkcs8" });

  return {
    publicKey,
    privateKey,
    publicKeyBase64: publicKeyDer.toString("base64"),
    privateKeyBase64: privateKeyDer.toString("base64"),
  };
}

/** Reconstructs a KeyObject from a base64 SPKI-encoded public key (as stored in DNS). 
 * Converts them into Base64
*/
export function publicKeyFromBase64(b64: string): KeyObject {
  const { createPublicKey } = require("crypto");
  return createPublicKey({
    key: Buffer.from(b64, "base64"),
    format: "der",
    type: "spki",
  });
}

/** Generates a random, URL-safe challenge string for the agent to sign. */
export function generateChallenge(): string {
  const { randomBytes } = require("crypto");
  return randomBytes(32).toString("base64url");
}