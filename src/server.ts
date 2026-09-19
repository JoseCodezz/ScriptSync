import "dotenv/config";
import { createApp } from "./app";

const app = createApp();

// Verification service on port 8081
const PORT = Number(process.env.PORT ?? 8081);
app.listen(PORT, () => {
  console.log(`ANS verification service listening on port ${PORT}`);
});
