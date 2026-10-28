"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const s = require("../src/settings");

test("a remote server must use https unless it is on this computer", () => {
  assert.equal(s.checkUrl("https://rooks.example.com"), "");
  assert.equal(s.checkUrl("http://localhost:3100"), "");
  assert.equal(s.checkUrl("http://127.0.0.1:3100"), "");
  assert.match(s.checkUrl("http://192.168.1.20:3100"), /must use https/);
  assert.match(s.checkUrl("https://me:pw@rooks.example.com"), /name or password/);
  assert.match(s.checkUrl("file:///etc/passwd"), /http or https/);
  assert.match(s.checkUrl("not a url"), /not a web address/);
});

test("settings are private, and missing ones fall back to defaults", () => {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "rooks-settings-")), "settings.json");
  const d = s.load(f);
  assert.equal(d.mode, "");
  assert.equal(s.serverUrl(d), "");
  s.save(f, { ...d, mode: "remote", remoteUrl: "https://rooks.example.com/" });
  if (process.platform !== "win32") assert.equal(fs.statSync(f).mode & 0o777, 0o600);
  assert.equal(s.serverUrl(s.load(f)), "https://rooks.example.com");
});
