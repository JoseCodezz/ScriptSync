import express from "express";
import { registerRouter } from "./routes/register";
import { registerExternalRouter } from "./routes/registerExternal";
import { registerGoDaddyAnsRouter } from "./routes/registerGoDaddyAns";
import { verifyRouter } from "./routes/verifyAgent"; // interactive /agents/challenge + /agents/verify
import { verifyCertLiveRouter } from "./routes/verifyCertLive";
import { contractVerifyRouter } from "./routes/verifyContract"; // POST /verify -- the assistant's contract
import { revokeRouter } from "./routes/revoke";
import { logRouter } from "./routes/log";

/**
 * Builds the Express app without binding a port, so tests (and anything
 * else that wants an in-process server) can import it directly instead of
 * spawning a real process. server.ts is the only thing that calls .listen().
 */
export function createApp(): express.Express {
  const app = express();
  app.use(express.json());

  app.get("/health", (_req, res) => res.json({ ok: true }));

  // Registration: register-external is the primary path for domains not
  // managed through the GoDaddy API (e.g. Porkbun, Namecheap). The GoDaddy
  // route stays available for a domain actually delegated to GoDaddy DNS.
  app.use("/agents", registerExternalRouter);
  app.use("/agents", registerRouter);
  app.use("/agents", registerGoDaddyAnsRouter);

  // Interactive live challenge/response (real proof-of-possession; not
  // called directly by the assistant, useful for testing).
  app.use("/agents", verifyRouter);
  app.use("/agents", revokeRouter);

  // The ONE contract endpoint assistant/verify.py::is_agent_verified() calls
  // (or would call, if the assistant is pointed at this service).
  app.use("/", contractVerifyRouter);
  app.use("/", logRouter);
  // verifyCertLiveRouter declares its own full path ("/agents/verify-live-cert"),
  // so it mounts at root, not under "/agents" (that would double the prefix).
  // It was written but never wired into the app until now.
  app.use("/", verifyCertLiveRouter);

  return app;
}
