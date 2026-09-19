import "dotenv/config";
import express from "express";
import { registerRouter } from "./routes/register";
import { registerExternalRouter } from "./routes/registerExternal";
import { verifyRouter } from "./routes/verifyAgent"; // interactive /agents/challenge + /agents/verify
import { contractVerifyRouter } from "./routes/verifyContract"; // POST /verify -- the assistant's contract
import { revokeRouter } from "./routes/revoke";
import { logRouter } from "./routes/log";

const app = express();
app.use(express.json());

app.get("/health", (_req, res) => res.json({ ok: true }));

// Interactive registration + live challenge/response (real proof-of-
// possession; not called directly by the assistant, useful for testing).
app.use("/agents", registerRouter);
app.use("/agents", registerExternalRouter);
app.use("/agents", verifyRouter);
app.use("/agents", revokeRouter);

// The ONE contract endpoint assistant/verify.py::is_agent_verified() calls.
app.use("/", contractVerifyRouter);
app.use("/", logRouter);

// Verification service on port 8081
const PORT = Number(process.env.PORT ?? 8081);
app.listen(PORT, () => {
  console.log(`ANS verification service listening on port ${PORT}`);
});