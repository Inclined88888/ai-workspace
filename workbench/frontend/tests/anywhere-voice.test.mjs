// Ask from anywhere, voice and meetings helpers (src/voice/logic.ts; feat/v02-anywhere-voice): `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const out = await build({ entryPoints: [new URL("../src/voice/logic.ts", import.meta.url).pathname], bundle: true, format: "esm", platform: "node", write: false });
const dir = fileURLToPath(new URL("../node_modules/.cache/wb-tests/", import.meta.url));
mkdirSync(dir, { recursive: true });
writeFileSync(dir + "voice-logic.mjs", out.outputFiles[0].text);
const v = await import(pathToFileURL(dir + "voice-logic.mjs").href);

test("only a clear yes approves; any no denies; everything else is asked again", () => {
  for (const yes of ["yes", "Yes, send it.", "yeah go ahead", "approve", "yes please", "send it", "Go for it!", "Yep, do it now"]) {
    assert.equal(v.spokenDecision(yes), "approve", yes);
  }
  for (const no of ["no", "No, don't send it", "yes... no wait", "hold on", "not yet", "cancel", "stop", "nope"]) {
    assert.equal(v.spokenDecision(no), "deny", no);
  }
  for (const unclear of ["", "okay", "sure", "maybe", "yes but change the subject", "send it to Sam instead", "what?", "yes?",
    "um yes", "yes send it to the whole team right now please thanks", "I guess so", "yes to the second one"]) {
    assert.equal(v.spokenDecision(unclear), "unclear", unclear);
  }
});

test("wake word is found at the start, loosely heard, and nowhere else", () => {
  assert.equal(v.afterWake("Hey Workspace, what's on today?", "hey workspace"), "what's on today");
  assert.equal(v.afterWake("hey work space", "hey workspace"), "");
  assert.equal(v.afterWake("a workspace add milk to my list", "hey workspace"), "add milk to my list");
  assert.equal(v.afterWake("okay workspace remind me", "hey workspace"), "remind me");
  assert.equal(v.afterWake("open my workspace settings", "hey workspace"), null);
  assert.equal(v.afterWake("what's the weather", "hey workspace"), null);
  assert.equal(v.afterWake("computer, lights", "computer"), "lights");
});

test("replies are spoken a sentence at a time without markdown, code or links", () => {
  const s = v.sentences("**Done.** I saved it to [Notes](/notes/3). See https://example.com for more.\n\n```js\nx()\n```\n- one\n- two");
  assert.deepEqual(s.slice(0, 3), ["Done.", "I saved it to Notes.", "See the link on screen for more."]);
  assert.ok(s.some((x) => x.includes("code on screen")));
  assert.ok(!s.join(" ").includes("*"));
  assert.ok(v.sentences("word ".repeat(200)).every((x) => x.length <= 280));
  assert.deepEqual(v.sentences("   "), []);
});

test("shortcut recorder makes Tauri accelerators and labels them for each platform", () => {
  const k = (key, code, mods = {}) => ({ key, code, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, ...mods });
  assert.equal(v.accelFromEvent(k(" ", "Space", { altKey: true })), "Alt+Space");
  assert.equal(v.accelFromEvent(k("k", "KeyK", { metaKey: true, shiftKey: true })), "Super+Shift+K");
  assert.equal(v.accelFromEvent(k("˚", "KeyK", { altKey: true })), "Alt+K"); // Option changes the character on a Mac
  assert.equal(v.accelFromEvent(k("Alt", "AltLeft", { altKey: true })), null);
  assert.equal(v.accelFromEvent(k("a", "KeyA")), null); // a bare letter would break typing everywhere
  assert.equal(v.accelFromEvent(k("F9", "F9")), "F9");
  assert.equal(v.accelLabel("Alt+Space", true), "⌥ Option Space");
  assert.equal(v.accelLabel("Alt+Space", false), "Alt + Space");
  assert.equal(v.accelLabel("Super+Shift+K", false), "Win + Shift + K");
});

test("meeting helpers: clock, action lines, plain-text notes, chat hand-off", () => {
  assert.equal(v.clock(0), "0:00");
  assert.equal(v.clock(75.4), "1:15");
  assert.equal(v.clock(3725), "1:02:05");
  assert.equal(v.actionMeta({ task: "x", owner: "", due: "" }), "No owner or date said");
  assert.match(v.actionMeta({ task: "x", owner: "Sam", due: "2026-11-06" }, new Date("2026-10-30T10:00:00")), /^Sam · Fri 6 Nov$/);
  assert.match(v.actionMeta({ task: "x", owner: "", due: "2027-01-04" }, new Date("2026-10-30T10:00:00")), /2027/);
  const txt = v.notesText("Weekly", { summary: "We met.", notes: ["A"], decisions: ["Ship Friday"], actions: [{ task: "Send deck", owner: "Sam", due: "2026-11-06" }, { task: "Book room", owner: "", due: "" }] });
  assert.match(txt, /Decisions\n- Ship Friday/);
  assert.match(txt, /- Send deck \(Sam, 2026-11-06\)/);
  assert.match(txt, /- Book room$/);
  const d = v.handoffDraft("Reply to this", "Hi, can we meet?", "Sure, Tuesday?");
  assert.match(d, /^Reply to this/);
  assert.match(d, /"""\nHi, can we meet\?\n"""/);
  assert.match(d, /You suggested:\nSure, Tuesday\?/);
});
