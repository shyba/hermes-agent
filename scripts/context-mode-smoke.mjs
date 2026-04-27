#!/usr/bin/env node
// End-to-end smoke test for the Hermes-installed context-mode MCP server.

import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [wrapperArg, contextModeArg, projectArg] = process.argv.slice(2);
const home = process.env.HOME || process.env.USERPROFILE || "";
const wrapper = resolve(wrapperArg || `${home}/.hermes/bin/context-mode`);
const contextModeDir = resolve(contextModeArg || "vendor/context-mode");
const projectDir = resolve(projectArg || process.cwd());

if (!existsSync(wrapper)) {
  console.error(`context-mode wrapper not found at ${wrapper}`);
  process.exit(1);
}

if (!existsSync(resolve(contextModeDir, "package.json"))) {
  console.error(`context-mode package.json not found at ${contextModeDir}`);
  process.exit(1);
}

const requireFromContextMode = createRequire(resolve(contextModeDir, "package.json"));
const { Client } = await import(
  pathToFileURL(requireFromContextMode.resolve("@modelcontextprotocol/sdk/client/index.js")).href
);
const { StdioClientTransport } = await import(
  pathToFileURL(requireFromContextMode.resolve("@modelcontextprotocol/sdk/client/stdio.js")).href
);

const transport = new StdioClientTransport({
  command: wrapper,
  args: [],
  cwd: projectDir,
  env: {
    ...process.env,
    PATH: `${resolve(home, ".hermes/bin")}:${process.env.PATH || ""}`,
    CONTEXT_MODE_PROJECT_DIR: projectDir,
  },
});

const client = new Client({
  name: "hermes-context-mode-smoke",
  version: "0.0.0",
});

try {
  await client.connect(transport);
  const tools = await client.listTools();
  const toolNames = tools.tools.map((tool) => tool.name);
  if (!toolNames.includes("ctx_execute")) {
    throw new Error(`ctx_execute not found. Tools: ${toolNames.join(", ")}`);
  }

  const result = await client.callTool({
    name: "ctx_execute",
    arguments: {
      language: "javascript",
      code: 'console.log(JSON.stringify({ spawned: true, cwd: process.cwd().includes(".ctx-mode-") }))',
      timeout: 5000,
    },
  });
  const text = result?.content?.[0]?.text || "";
  if (!text.includes('"spawned":true')) {
    throw new Error(`ctx_execute returned unexpected output: ${text}`);
  }

  console.log(JSON.stringify({
    ok: true,
    toolCount: toolNames.length,
    firstTools: toolNames.slice(0, 8),
    executeOutput: text.trim(),
  }, null, 2));
} finally {
  await client.close();
}
