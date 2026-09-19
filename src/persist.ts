/**
 * Minimal file-backed persistence for the agent registry and public log, so
 * a server restart doesn't silently wipe every registration (previously
 * pure in-memory -- fine for a single demo run, risky mid-demo).
 *
 * Not a database: small JSON files under data/ (gitignored), rewritten
 * whole on every write. That's the right tradeoff at this scale (a handful
 * of agents, occasional writes) -- anything fancier is premature here.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { join } from "path";

// Read lazily (not cached at module load) so tests can point this at a
// throwaway directory via ANS_DATA_DIR before touching the store/log,
// keeping test runs from writing into the real server's data/ directory.
function dataDir(): string {
  return process.env.ANS_DATA_DIR ?? join(__dirname, "..", "data");
}

export function loadJson<T>(filename: string, fallback: T): T {
  const path = join(dataDir(), filename);
  if (!existsSync(path)) return fallback;
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch {
    return fallback;
  }
}

export function saveJson(filename: string, data: unknown): void {
  const dir = dataDir();
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, filename), JSON.stringify(data, null, 2));
}
