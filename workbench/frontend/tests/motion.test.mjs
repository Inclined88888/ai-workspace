// Unit tests for the motion system (src/motion): pure helpers plus the CSS contract every theme relies on.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const src = (p) => new URL(`../src/${p}`, import.meta.url).pathname;
const out = await build({ entryPoints: [src("motion/index.tsx")], bundle: true, format: "esm", platform: "node", write: false,
  external: ["react", "react-dom", "react-router-dom", "marked", "dompurify"], loader: { ".css": "empty" }, jsx: "automatic" });
const dir = fileURLToPath(new URL("../node_modules/.cache/wb-tests/", import.meta.url));
mkdirSync(dir, { recursive: true });
writeFileSync(dir + "motion.mjs", out.outputFiles[0].text);
globalThis.document ??= { documentElement: { dataset: {} } };
globalThis.window ??= { matchMedia: () => ({ matches: false }) };
const m = await import(pathToFileURL(dir + "motion.mjs").href);
const tokens = readFileSync(src("motion/tokens.css"), "utf8");
const sig = readFileSync(src("motion/signature.css"), "utf8");

test("wordEnds marks the end of every word, keeping punctuation with its word", () => {
  const t = "Hello, world. Two  spaces\nand **bold**.";
  const ends = m.wordEnds(t);
  assert.equal(ends.length, 6);
  assert.equal(t.slice(0, ends[0]), "Hello, ", "a word owns its trailing space, so a half-revealed line never ends mid-gap");
  assert.equal(t.slice(0, ends.at(-1)).trimEnd(), t);
  assert.deepEqual(m.wordEnds(""), []);
});

test("wordsAt reveals at reading pace and never takes longer than the cap", () => {
  assert.equal(m.wordsAt(0, 10), 1);
  assert.equal(m.wordsAt(28 * 5, 10), 6);
  assert.equal(m.wordsAt(10_000, 10), 10);
  assert.equal(m.wordsAt(1600, 400), 400, "a long reply still lands within 1.6 s");
  assert.equal(m.wordsAt(100, 0), 0);
});

test("parsePointer reads Rooks Desk step results as fractions of the screenshot", () => {
  assert.deepEqual(m.parsePointer("click at (640, 200) on button 'Send'", "click", 1280, 800), { x: 0.5, y: 0.25, kind: "click" });
  assert.deepEqual(m.parsePointer("double click at (0, 800)", "double_click", 1280, 800), { x: 0, y: 1, kind: "click" });
  assert.deepEqual(m.parsePointer("moved to (320, 400)", "move", 1280, 800), { x: 0.25, y: 0.5, kind: "move" });
  assert.deepEqual(m.parsePointer("dragged (10, 10) -> (1280, 400)", "drag", 1280, 800), { x: 1, y: 0.5, kind: "move" });
  assert.equal(m.parsePointer("typed 12 characters", "type", 1280, 800), null);
  assert.equal(m.parsePointer("click at (5, 5)", "click", 0, 0), null, "no size yet, no cursor");
  assert.equal(m.parsePointer("a photo of (3, 4)", "click", 10, 10), null);
});

test("every theme gets its own motion personality from tokens", () => {
  for (const t of ["sunshine", "paper", "cherry", "neon", "dark", "contrast"]) assert.match(tokens, new RegExp(`\\[data-theme="${t}"\\]`), t);
  for (const k of ["--m-1", "--m-3", "--ease-standard", "--ease-expo", "--spring-gentle", "--spring-settle", "--spring-snappy", "--m-glow-a"]) assert.ok(tokens.includes(`${k}:`), k);
  assert.match(tokens, /linear\(/, "springs are sampled curves");
});

test("Calm, Off and the OS reduced-motion setting are all honoured", () => {
  assert.match(tokens, /\[data-motion="subtle"\][^{]*\{[^}]*--m-travel:\s*0/);
  assert.match(sig, /\[data-motion="off"\]/);
  assert.match(sig, /@media \(prefers-reduced-motion: reduce\)/);
  for (const c of [".edge-glow", ".shimmer-text", ".cursor-track", ".voice-orb", ".done-check", ".w-in"]) assert.ok(sig.includes(c), c);
});

test("only compositor-friendly properties are animated", () => {
  // keyframe blocks are one line each: "@keyframes name { from { … } to { … } }"
  const frames = [...sig.matchAll(/@keyframes\s+[\w-]+\s*\{(.*)\}\s*$/gm)].map((x) => x[1]);
  assert.ok(frames.length >= 8);
  const props = new Set(frames.flatMap((f) => [...f.matchAll(/([a-z-]+)\s*:/g)].map((x) => x[1])));
  const ok = new Set(["opacity", "transform", "scale", "translate", "rotate", "stroke-dashoffset", "background-position", "offset"]);
  for (const p of props) assert.ok(ok.has(p), `@keyframes animates ${p}`);
});
