#!/usr/bin/env node
// Hermes-owned launcher for the vendored context-mode MCP server.
//
// The upstream stdio transport can finish startup with no referenced Node
// handles in some runtimes, which makes the server exit with code 0 before an
// MCP client can initialize. Keep one harmless referenced timer alive while the
// vendored server owns protocol handling and signal cleanup.

import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "..");
const contextModeDir = process.env.HERMES_CONTEXT_MODE_DIR
  ? resolve(process.env.HERMES_CONTEXT_MODE_DIR)
  : resolve(repoRoot, "vendor/context-mode");
const entrypoint = resolve(contextModeDir, "start.mjs");

if (!existsSync(entrypoint)) {
  console.error(`context-mode start.mjs not found at ${entrypoint}`);
  process.exit(1);
}

const keepAlive = setInterval(() => {}, 0x7fffffff);

try {
  await import(pathToFileURL(entrypoint).href);
} catch (error) {
  clearInterval(keepAlive);
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
}
