// Unit tests for the pack page helpers (src/packs/logic.ts): `npm test`. feat/v02-packs
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const out = await build({ entryPoints: [new URL("../src/packs/logic.ts", import.meta.url).pathname], bundle: true, format: "esm", platform: "node", write: false });
const dir = fileURLToPath(new URL("../node_modules/.cache/wb-tests/", import.meta.url));
mkdirSync(dir, { recursive: true });
writeFileSync(dir + "packs.mjs", out.outputFiles[0].text);
const t = await import(pathToFileURL(dir + "packs.mjs").href);

const S = { tax_registered: true, tax_name: "GST", tax_rate: 10, prices_include_tax: false, materials_markup: 20, labour_markup: 0, currency: "AUD", currency_symbol: "$" };

test("live totals match the server: markup on materials, GST on top", () => {
  const r = t.previewTotals([{ kind: "labour", description: "Install", qty: 2, unit: "hour", unit_cost: 95 },
                             { kind: "materials", description: "Downlight", qty: 3, unit: "each", unit_cost: 45 }], S);
  assert.equal(r.labour, 190); assert.equal(r.materials, 162); assert.equal(r.subtotal_ex_tax, 352); assert.equal(r.tax, 35.2); assert.equal(r.total, 387.2);
});

test("GST-inclusive prices and not registered for GST", () => {
  const inc = t.previewTotals([{ kind: "other", description: "x", qty: 1, unit: "each", unit_cost: 110 }], { ...S, prices_include_tax: true });
  assert.equal(inc.total, 110); assert.equal(inc.tax, 10);
  const none = t.previewTotals([{ kind: "other", description: "x", qty: 1, unit: "each", unit_cost: 100 }], { ...S, tax_registered: false });
  assert.equal(none.tax, 0); assert.equal(none.total, 100);
});

test("ABN checksum", () => {
  assert.equal(t.abnValid("51 824 753 556"), true);
  assert.equal(t.abnValid("51 824 753 557"), false);
  assert.equal(t.abnValid("123"), false);
});

test("labels read in plain English", () => {
  assert.equal(t.money(1234.5), "$1,234.50");
  assert.equal(t.statusLabel("invoice", "sent", 3), "Overdue 3 days");
  assert.equal(t.statusLabel("quote", "accepted", 0), "Accepted");
  assert.equal(t.tierLabel("free", true), "Free");
  assert.equal(t.tierLabel("pro", false), "Pro · upgrade to use");
  assert.equal(t.deckProgress(10, 0), "10 cards · all caught up");
  assert.equal(t.deckProgress(10, 4), "4 of 10 due now");
});

test("review keys, working lines and references", () => {
  assert.equal(t.gradeForKey("1"), "again"); assert.equal(t.gradeForKey("4"), "easy"); assert.equal(t.gradeForKey("x"), null);
  assert.deepEqual(t.workingLines("2x + 4 = 10\n\n=> 2x = 6\n∴ x = 3"), ["2x + 4 = 10", "2x = 6", "x = 3"]);
  assert.equal(t.mathBlock("x^2"), "$$x^2$$");
  assert.deepEqual(t.parseAuthors("Nguyen, Anh; Smith, Kate"), ["Nguyen, Anh", "Smith, Kate"]);
  assert.equal(t.authorsText([{ family: "Nguyen", given: "Anh" }]), "Nguyen, A.");
});
