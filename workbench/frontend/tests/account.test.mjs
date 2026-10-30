// Unit tests for the plan checks in src/account.ts: Free is never blocked, Pro unlocks, beta unlocks everything.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const out = await build({ entryPoints: [new URL("../src/account.ts", import.meta.url).pathname], bundle: true, format: "esm", platform: "node", write: false, external: ["react"] });
const dir = fileURLToPath(new URL("../node_modules/.cache/wb-tests/", import.meta.url));
mkdirSync(dir, { recursive: true });
const file = dir + "account.mjs";
writeFileSync(file, out.outputFiles[0].text);
const { hasFeature, themeLocked } = await import(pathToFileURL(file).href);

const pro = [{ id: "computer_use", label: "Computer use", unlocked: false }, { id: "premium_themes", label: "Themes", unlocked: false }];
const free = { billing_live: true, features: ["app", "byo_keys", "ollama", "basic_themes"], pro_features: pro, premium_themes: ["neon", "cherry"] };

test("an unknown or still-loading account never blocks anything", () => {
  assert.equal(hasFeature(null, "computer_use"), true);
  assert.equal(themeLocked(null, "neon"), false);
});

test("Free features always pass; Pro features need Pro once billing is live", () => {
  assert.equal(hasFeature(free, "byo_keys"), true);
  assert.equal(hasFeature(free, "something_new"), true); // not a Pro feature: never gated
  assert.equal(hasFeature(free, "computer_use"), false);
  assert.equal(hasFeature({ ...free, features: [...free.features, "computer_use"] }, "computer_use"), true);
});

test("while billing is not live everything is unlocked", () => {
  assert.equal(hasFeature({ ...free, billing_live: false }, "computer_use"), true);
  assert.equal(themeLocked({ ...free, billing_live: false }, "neon"), false);
});

test("premium themes lock on Free but the default theme never does", () => {
  assert.equal(themeLocked(free, "neon"), true);
  assert.equal(themeLocked(free, "paper"), false);
  assert.equal(themeLocked({ ...free, premium_themes: ["paper"] }, "paper", "paper"), false);
  assert.equal(themeLocked({ ...free, features: [...free.features, "premium_themes"] }, "neon"), false);
});
