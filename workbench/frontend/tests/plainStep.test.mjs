import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const out = await build({
  entryPoints: [new URL("../src/plainStep.ts", import.meta.url).pathname],
  bundle: true, format: "esm", platform: "node", write: false,
});
const dir = fileURLToPath(new URL("../node_modules/.cache/wb-tests/", import.meta.url));
mkdirSync(dir, { recursive: true });
const file = dir + "plainStep.mjs";
writeFileSync(file, out.outputFiles[0].text);
const { plainStep } = await import(pathToFileURL(file).href);

test("a search is a sentence, not the tool name", () => {
  assert.equal(plainStep("web_search", JSON.stringify({ query: "timber prices" })), "Searching: timber prices");
  assert.equal(plainStep("run_command", JSON.stringify({ command: "rm -rf /tmp" })), "Running something on this computer");
  assert.equal(plainStep("mystery_tool", "{\"raw\":true}"), "Working");
});
