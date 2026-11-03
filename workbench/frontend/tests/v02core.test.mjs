// feat/v02-core: Projects, History and Checked answers stay on theme tokens and are reachable from the nav.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");

test("v02 styles use theme variables only (every theme restyles them)", () => {
  const css = read("../src/v02/v02.css").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b/i, "no hex colours");
  assert.doesNotMatch(css, /\b(rgb|rgba|hsl|hsla)\(/i, "no literal colours");
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /data-motion="full"/, "animations only under full motion");
});

test("Projects and History are in the nav and routed", () => {
  const app = read("../src/App.tsx");
  for (const path of ["/projects", "/history"]) {
    assert.ok(app.includes(`{ to: "${path}"`), `${path} in NAV`);
    assert.ok(app.includes(`<Route path="${path}"`), `${path} routed`);
  }
});

test("chat has the project switcher, the check toggle and Check this", () => {
  const chat = read("../src/pages/Assistant.tsx");
  for (const c of ["<ProjectSwitcher", "<CheckToggle", "<CheckThis"]) assert.ok(chat.includes(c), c);
});

test("Pro features show the upgrade card on 402 instead of an error", () => {
  const files = ["../src/pages/Projects.tsx", "../src/pages/History.tsx", "../src/v02/ChatExtras.tsx"].map(read).join("\n");
  for (const f of ["projects_unlimited", "checked_answers"]) assert.ok(files.includes(`feature="${f}"`), f);
  // integration/v0.2: exports are never gated, so History has no upgrade card for its CSV / PDF export
  assert.ok(!files.includes(`feature="action_history_export"`), "history export is free");
});
