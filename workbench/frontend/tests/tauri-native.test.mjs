// feat/tauri-native: OS notifications, tray routing and aiworkspace:// links reach the page safely.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const desktop = read("../src/desktop.ts");
const bridge = read("../src/DesktopBridge.tsx");
const chat = read("../src/pages/Assistant.tsx");
const jobs = read("../src/v02/JobNotifier.tsx");
const app = read("../src/App.tsx");
const native = read("../../../desktop/src-tauri/src/native.rs");
const caps = read("../../../desktop/src-tauri/capabilities/default.json");
const build = read("../../../desktop/src-tauri/build.rs");

test("notify is native in the desktop app and the Web Notification API in a browser", () => {
  assert.match(desktop, /invoke<void>\("notify", \{ title, body \}\)/);
  assert.match(desktop, /Notification\.permission !== "granted"\) return/);
  assert.match(desktop, /Notification\.requestPermission\(\)/);
});

test("a finished reply and a held approval notify only while the page is in the background", () => {
  assert.match(chat, /if \(inBackground\(\)\) void notify\("Approval needed"/);
  assert.match(chat, /!stopped && lastReply && inBackground\(\)\) void notify\("Reply ready"/);
  assert.match(chat, /askNotifyPermission\(\);/);
  assert.match(jobs, /if \(inBackground\(\)\) void notify\(n\.title/);
});

test("links only navigate to plain app paths and only pre-fill the chat", async () => {
  const m = bridge.match(/export const safePath = \(p: unknown\): p is string => (.+);\n/);
  assert.ok(m, "safePath present");
  const safePath = new Function("p", `return ${m[1]};`);
  for (const ok of ["/", "/assistant", "/assistant/12", "/automations"]) assert.equal(safePath(ok), true, ok);
  for (const bad of ["//evil.com", "/api/settings", "/API", "https://x", "/a?b=1", "/a/../b", "", 3]) assert.equal(safePath(bad), false, String(bad));
  assert.match(bridge, /state: \{ draft:/);
  assert.doesNotMatch(bridge, /\/assistant\/chat/); // never sends
  assert.match(app, /<DesktopBridge \/>/);
});

test("shell side: commands are granted, links are parsed strictly, close goes to the tray", () => {
  for (const cmd of ["notify", "desktop_ready"]) assert.match(build, new RegExp(`"${cmd}"`));
  assert.match(caps, /"allow-notify"/);
  assert.match(caps, /"allow-desktop-ready"/);
  assert.match(native, /fn parse_link\(url: &Url\) -> Option<Link>/);
  assert.match(native, /api\.prevent_close\(\)/);
  assert.match(native, /"quit" => quit\(app\)/);
});
