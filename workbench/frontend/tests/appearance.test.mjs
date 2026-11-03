// Unit tests for the Appearance store (src/appearance.tsx), run with Node's built-in runner: `npm test`.
// esbuild (already installed with Vite) compiles the module; the browser globals are small stubs.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const out = await build({ entryPoints: [new URL("../src/appearance.tsx", import.meta.url).pathname], bundle: true, format: "esm", platform: "node", write: false, external: ["react"] });
// Written under node_modules so the external `react` import resolves.
const dir = fileURLToPath(new URL("../node_modules/.cache/wb-tests/", import.meta.url));
mkdirSync(dir, { recursive: true });
const file = dir + "appearance.mjs";
writeFileSync(file, out.outputFiles[0].text);

const store = new Map();
let reduced = false;
let darkOS = false;
const events = [];
function fakeEl() {
  const props = new Map();
  return { dataset: {}, style: { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k), props } };
}
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
globalThis.document = { documentElement: fakeEl() };
globalThis.window = { matchMedia: (q) => ({ matches: q.includes("reduced-motion") ? reduced : q.includes("dark") ? darkOS : false }), dispatchEvent: (e) => events.push(e) };
globalThis.CustomEvent ??= class extends Event { constructor(t, o) { super(t); this.detail = o?.detail; } };

const ap = await import(pathToFileURL(file).href);

beforeEach(() => { store.clear(); reduced = false; darkOS = false; events.length = 0; document.documentElement = fakeEl(); });

test("defaults to Sunshine, Clean type, 14px, full motion, the theme's own accent", () => {
  assert.deepEqual(ap.readAppearance(), ap.DEFAULTS);
  assert.deepEqual([ap.DEFAULTS.theme, ap.DEFAULTS.font, ap.DEFAULTS.accent], ["sunshine", "modern", "theme"]);
});

test("the curated six plus Match my system, Sunshine first", () => {
  assert.deepEqual(ap.THEMES.map((t) => t.id), ["sunshine", "paper", "cherry", "neon", "night", "contrast", "auto"]);
  for (const t of ap.THEMES) for (const c of Object.values(t.swatch)) assert.match(c, /^#[0-9a-f]{6}$/);
});

test("the older themes stay available, and saved choices are kept", () => {
  store.set("wb-appearance", JSON.stringify({ theme: "linen" }));
  assert.equal(ap.readAppearance().theme, "linen", "Linen and Gallery still load for anyone who chose them");
  store.set("wb-appearance", JSON.stringify({ theme: "paper", font: "editorial", accent: "cherry" }));
  const a = ap.readAppearance();
  assert.deepEqual([a.theme, a.font, a.accent], ["paper", "editorial", "cherry"]);
});

test("corrupt or hostile storage falls back to safe values", () => {
  store.set("wb-appearance", "{not json");
  assert.deepEqual(ap.readAppearance(), ap.DEFAULTS);
  store.set("wb-appearance", JSON.stringify({ theme: "purple", font: "<script>", size: 400, motion: "wild", accent: "neon", density: "x" }));
  assert.deepEqual(ap.readAppearance(), { ...ap.DEFAULTS, size: ap.SIZES.max });
  store.set("wb-appearance", JSON.stringify({ size: -3 }));
  assert.equal(ap.readAppearance().size, ap.SIZES.min);
});

test("the old dark-theme setting migrates to Night", () => {
  store.set("wb-theme", "dark");
  assert.equal(ap.readAppearance().theme, "night");
});

test("applyTo writes data attributes and scales text tokens", () => {
  const el = fakeEl();
  ap.applyTo(el, { ...ap.DEFAULTS, theme: "night", font: "classic", size: 16, density: "compact", motion: "subtle", accent: "cobalt" });
  assert.deepEqual({ ...el.dataset }, { theme: "dark", mode: "dark", font: "classic", density: "compact", motion: "subtle", accent: "cobalt" });
  assert.equal(el.style.props.get("--text-md"), "16px");
  assert.equal(el.style.props.get("--text-4xl"), "64px");
  ap.applyTo(el, ap.DEFAULTS);
  assert.equal(el.dataset.theme, "sunshine");
  assert.equal(el.dataset.mode, "light");
  ap.applyTo(el, { ...ap.DEFAULTS, theme: "paper" });
  assert.equal(el.dataset.theme, "paper");
  assert.equal(el.style.props.size, 0, "14px removes overrides");
});

test("setAppearance persists, applies live and notifies open pages", () => {
  ap.setAppearance({ theme: "linen", motion: "off" });
  assert.equal(JSON.parse(store.get("wb-appearance")).theme, "linen");
  assert.equal(document.documentElement.dataset.theme, "linen");
  assert.equal(events.at(-1).type, "wb:appearance");
  assert.equal(events.at(-1).detail.motion, "off");
  assert.equal(store.has("wb-theme"), false);
});

test("motion is off when the user chooses Off or the OS asks for reduced motion", () => {
  ap.applyTo(document.documentElement, ap.DEFAULTS);
  assert.equal(ap.motionOff(), false);
  reduced = true;
  assert.equal(ap.motionOff(), true);
  reduced = false;
  ap.applyTo(document.documentElement, { ...ap.DEFAULTS, motion: "off" });
  assert.equal(ap.motionOff(), true);
});

test("Neon and High contrast always keep their own accent", () => {
  const el = fakeEl();
  ap.applyTo(el, { ...ap.DEFAULTS, theme: "neon", accent: "cobalt" });
  assert.deepEqual([el.dataset.theme, el.dataset.mode, el.dataset.accent], ["neon", "dark", "theme"]);
  ap.applyTo(el, { ...ap.DEFAULTS, theme: "contrast", accent: "sun" });
  assert.deepEqual([el.dataset.theme, el.dataset.mode, el.dataset.accent], ["contrast", "light", "theme"]);
  ap.applyTo(el, { ...ap.DEFAULTS, theme: "cherry", accent: "cobalt" });
  assert.equal(el.dataset.accent, "cobalt", "light themes still take any accent");
});

test("Match my system follows the OS: Sunshine by day, Dark at night", () => {
  const el = fakeEl();
  ap.applyTo(el, { ...ap.DEFAULTS, theme: "auto" });
  assert.equal(el.dataset.theme, "sunshine");
  darkOS = true;
  ap.applyTo(el, { ...ap.DEFAULTS, theme: "auto" });
  assert.deepEqual([el.dataset.theme, el.dataset.mode], ["dark", "dark"]);
  assert.equal(ap.isDark("auto"), true);
  assert.equal(ap.resolveTheme("neon"), "neon");
});

test("picking a theme resets the accent and tells subscribers (window chrome, backend copy)", () => {
  const seen = [];
  const off = ap.onAppearance((a) => seen.push(a.theme));
  ap.setAppearance({ accent: "cobalt" });
  ap.pickTheme("neon");
  off();
  ap.pickTheme("paper");
  assert.deepEqual(seen, ["sunshine", "neon"]);
  assert.equal(ap.readAppearance().accent, "theme");
  assert.equal(ap.readAppearance().theme, "paper");
});
