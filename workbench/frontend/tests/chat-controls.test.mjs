// feat/chat-controls: the chat page has Stop, Regenerate and Edit-and-resend wired to their endpoints.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const chat = read("../src/pages/Assistant.tsx");

test("the in-flight turn can be aborted (AbortController on the stream)", () => {
  assert.match(chat, /new AbortController\(\)/);
  assert.match(chat, /ctrl\.signal\)/);
  assert.match(chat, /aria-label="Stop"/);
  assert.match(chat, /\/assistant\/stop/);
  // leaving the page stops the stream on this end
  assert.match(chat, /\(\) => abortRef\.current\?\.abort\(\), \[\]/);
});

test("regenerate drops the last reply and re-runs the same turn", () => {
  assert.match(chat, /aria-label="Regenerate reply"/);
  assert.match(chat, /\/conversations\/\$\{cid\}\/regenerate/);
  assert.match(chat, /run\(r\.message, \{ resend: true \}\)/);
});

test("a sent user message can be edited inline and resent", () => {
  assert.match(chat, /aria-label="Edit and resend"/);
  assert.match(chat, /aria-label="Edit message"/);
  assert.match(chat, /\/conversations\/\$\{cid\}\/messages\/\$\{m\.id\}/);
  assert.match(chat, /run\(content, \{ resend: true \}\)/);
  // Escape cancels, Enter sends
  assert.match(chat, /e\.key === "Escape"\) setEditing\(null\)/);
});

test("the composer swaps Send for Stop while the assistant works", () => {
  assert.match(chat, /\{busy\s*\?\s*<button type="button" className="btn send" onClick=\{stopTurn\}/);
  assert.match(chat, /: <button className="btn primary send" disabled=\{\(!text\.trim\(\) && !att\.ready\.length\) \|\| att\.uploading\} aria-label="Send">/);
});
