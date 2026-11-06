// Loads the unmodified cloudflare/worker.js into a node:vm context so tests can call
// its top-level functions directly. No bundler or npm dependency is needed.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(here, "..", "..");

// Tests stay offline: the default fetch answers 503 instead of reaching a portal.
// Reassign ctx.fetch in a test to observe or fake network calls.
const offlineFetch = async () => new Response("offline", { status: 503 });

export function loadWorker() {
  const workerPath = path.join(repoRoot, "cloudflare", "worker.js");
  const src = fs
    .readFileSync(workerPath, "utf8")
    .replace(/^export default\s+/m, "globalThis.__worker = ");
  const ctx = vm.createContext({
    console, URL, Response, Request, Headers, crypto, TextEncoder, TextDecoder, setTimeout,
    fetch: offlineFetch
  });
  vm.runInContext(src, ctx, { filename: workerPath });
  return ctx;
}

export function loadGoldenPosts() {
  const p = path.join(repoRoot, "tests", "fixtures", "xray_fb_posts.json");
  return JSON.parse(fs.readFileSync(p, "utf8"));
}
