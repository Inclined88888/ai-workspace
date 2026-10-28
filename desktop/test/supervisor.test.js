"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const { Supervisor, detectCompose, healthy } = require("../src/supervisor");

const FAKE = path.join(__dirname, "fake_server.js");
const freePort = () => new Promise((r) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); }); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (fn, ms = 10000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await fn()) return true; await sleep(100); } return false; };

function make(port, extra = {}, env = {}) {
  return new Supervisor({ strategy: "process", url: `http://127.0.0.1:${port}`, command: process.execPath, args: [FAKE, String(port)], env, ...extra },
    { checkEvery: 200, backoff: [100, 200, 300], startTimeout: 5000 });
}

test("starts the server, reports running, and stops what it started", async () => {
  const port = await freePort();
  const sup = make(port);
  assert.equal(await sup.start(), true);
  assert.equal(sup.state, "running");
  assert.ok(await healthy(`http://127.0.0.1:${port}`));
  await sup.stop();
  assert.equal(sup.state, "stopped");
  assert.ok(await until(async () => !(await healthy(`http://127.0.0.1:${port}`))), "the server is gone after stop");
});

test("a server that crashes is restarted and answers again", async () => {
  const port = await freePort();
  const sup = make(port);
  const states = [];
  sup.on("state", (s) => states.push(s.state));
  await sup.start();
  process.kill(-sup.child.pid, "SIGKILL");  // the crash
  assert.ok(await until(() => states.includes("restarting")), "it notices");
  assert.ok(await until(() => sup.state === "running" && states.lastIndexOf("running") > states.indexOf("restarting")), "and it is back");
  assert.ok(await healthy(`http://127.0.0.1:${port}`));
  await sup.stop();
});

test("a server that keeps crashing is given up on with a reason, not restarted forever", async () => {
  const port = await freePort();
  const sup = make(port, {}, { FAKE_DIE_AFTER_MS: "300" });
  await sup.start();
  assert.ok(await until(() => sup.state === "failed", 15000), sup.detail);
  assert.match(sup.detail, /keeps stopping/);
  await sup.stop();
});

test("a server that never answers fails with what was tried", async () => {
  const port = await freePort();
  const sup = make(port, {}, { FAKE_NEVER_HEALTHY: "1" });
  sup.startTimeout = 1500;
  assert.equal(await sup.start(), false);
  assert.equal(sup.state, "failed");
  assert.match(sup.detail, /did not answer/);
  await sup.stop();
});

test("a server that was already running is used and left running on quit", async () => {
  const port = await freePort();
  const outside = make(port);
  await outside.start();
  const sup = make(port);
  assert.equal(await sup.start(), true);
  assert.equal(sup.startedByUs, false);
  await sup.stop();
  assert.ok(await healthy(`http://127.0.0.1:${port}`), "still running: the app did not start it");
  await outside.stop();
});

test("stopOnQuit false leaves the server running", async () => {
  const port = await freePort();
  const sup = make(port, { stopOnQuit: false });
  await sup.start();
  const pid = sup.child.pid;
  await sup.stop();
  assert.ok(await healthy(`http://127.0.0.1:${port}`));
  process.kill(-pid, "SIGTERM");
});

test("the standard install's address comes from its .env", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "rooks-install-"));
  assert.equal(detectCompose(dir), null);
  fs.writeFileSync(path.join(dir, "docker-compose.yml"), "services: {}\n");
  assert.equal(detectCompose(dir).url, "http://127.0.0.1:3100");
  fs.writeFileSync(path.join(dir, ".env"), "WORKBENCH_PORT=3200\nBIND_ADDRESS=0.0.0.0\n");
  assert.equal(detectCompose(dir).url, "http://127.0.0.1:3200");
});

test("a compose start without Docker says so", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "rooks-install-"));
  fs.writeFileSync(path.join(dir, "docker-compose.yml"), "services: {}\n");
  const port = await freePort();
  const sup = new Supervisor({ strategy: "compose", dir, url: `http://127.0.0.1:${port}` }, { startTimeout: 1000 });
  const saved = process.env.PATH;
  process.env.PATH = "/nonexistent";
  try {
    assert.equal(await sup.start(), false);
  } finally {
    process.env.PATH = saved;
  }
  assert.equal(sup.state, "failed");
  assert.match(sup.detail, /Docker is not installed|Docker is not running/);
});
