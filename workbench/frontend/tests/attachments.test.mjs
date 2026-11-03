// feat/attachments: the composer takes files (button, drag and drop, paste) and sent messages show them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const chat = read("../src/pages/Assistant.tsx");
const att = read("../src/Attachments.tsx");
const css = read("../src/attachments.css");

test("composer: attach button, drop and paste all feed the same queue", () => {
  assert.match(chat, /<AttachButton onFiles=\{att\.add\}/);
  assert.match(chat, /onDrop=\{\(e\) => \{ const f = filesFrom\(e\.dataTransfer\)/);
  assert.match(chat, /onPaste=\{\(e\) => \{ const f = filesFrom\(e\.clipboardData\)/);
  assert.match(chat, /<PendingChips items=\{att\.items\} onRemove=\{att\.remove\}/);
});

test("files upload first, then the turn carries their ids; sending waits for uploads", () => {
  assert.match(att, /api<Att>\("\/assistant\/attachments"/);
  assert.match(chat, /attachments: \(opts\.files \?\? \[\]\)\.map\(\(a\) => a\.id\)/);
  assert.match(chat, /if \(\(!msg && !files\.length\) \|\| busy \|\| att\.uploading\) return;/);
  assert.match(att, /f\.size > MAX_BYTES/);
  assert.match(att, /MAX_FILES - cur\.length/);
});

test("history shows thumbnails and document links from the server", () => {
  assert.match(chat, /<MessageAttachments items=\{m\.attachments\} \/>/);
  assert.match(att, /\/api\/assistant\/attachments\/\$\{encodeURIComponent\(a\.id\)\}/);
  assert.match(att, /rel="noreferrer"/);
});

test("attachment styles use theme variables only", () => {
  assert.doesNotMatch(css.replace(/\/\*[\s\S]*?\*\//g, ""), /#[0-9a-fA-F]{3,8}\b|rgb\(/);
});
