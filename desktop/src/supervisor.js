"use strict";
/**
 * Looks after the local ROOKS server: starts it, checks that it answers, restarts it if it stops, and stops what it
 * started when you quit. Two ways to run ROOKS on this computer:
 *
 *   compose  the standard install (install.sh): `docker compose up -d workbench` in the install folder.
 *   process  a command you give (for example a Python install without Docker); it is a child of the app.
 *
 * Restarts back off (1, 2, 4, 8, 16 seconds) and give up after 5 failures within 2 minutes, so a server that cannot
 * start does not spin forever; the window then says what failed and offers Retry.
 */
const { spawn } = require("node:child_process");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const https = require("node:https");

const BACKOFF = [1000, 2000, 4000, 8000, 16000];
const WINDOW_MS = 120000;

function readEnvFile(file) {
  const out = {};
  try {
    for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
      const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  } catch { /* no .env: defaults apply */ }
  return out;
}

/** What the standard install in `dir` looks like: its address comes from its .env (WORKBENCH_PORT, BIND_ADDRESS). */
function detectCompose(dir) {
  const compose = path.join(dir, "docker-compose.yml");
  if (!fs.existsSync(compose)) return null;
  const env = readEnvFile(path.join(dir, ".env"));
  const host = !env.BIND_ADDRESS || env.BIND_ADDRESS === "0.0.0.0" ? "127.0.0.1" : env.BIND_ADDRESS;
  return { dir, url: `http://${host}:${env.WORKBENCH_PORT || "3100"}` };
}

/** GET url/healthz; resolves true when ROOKS answers 200 within the timeout. */
function healthy(url, timeoutMs = 3000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    try {
      const u = new URL("/healthz", url);
      const req = (u.protocol === "https:" ? https : http).get(u, { timeout: timeoutMs }, (res) => {
        res.resume();
        finish(res.statusCode === 200);
      });
      req.on("timeout", () => { req.destroy(); finish(false); });
      req.on("error", () => finish(false));
    } catch { finish(false); }
  });
}

function run(cmd, args, opts) {
  return new Promise((resolve) => {
    let out = "";
    const p = spawn(cmd, args, { ...opts, stdio: ["ignore", "pipe", "pipe"] });
    p.stdout.on("data", (d) => { out += d; });
    p.stderr.on("data", (d) => { out += d; });
    p.on("error", (e) => resolve({ code: -1, out: String(e.message) }));
    p.on("close", (code) => resolve({ code, out: out.slice(-4000) }));
  });
}

class Supervisor extends EventEmitter {
  /**
   * @param {object} cfg  { strategy: "compose"|"process", url, dir?, command?, args?, cwd?, env?, stopOnQuit?, logFile? }
   */
  constructor(cfg, deps = {}) {
    super();
    this.cfg = cfg;
    this.state = "stopped";        // stopped | starting | running | restarting | failed
    this.detail = "";
    this.child = null;
    this.startedByUs = false;
    this.failures = [];
    this.timer = null;
    this.stopping = false;
    this.recovering = false;
    this.healthy = deps.healthy || healthy;
    this.checkEvery = deps.checkEvery || 3000;
    this.startTimeout = deps.startTimeout || 180000;
    this.backoff = deps.backoff || BACKOFF;
  }

  set(state, detail = "") {
    this.state = state;
    this.detail = detail;
    this.emit("state", { state, detail, attempts: this.failures.length });
  }

  log(text) {
    if (!this.cfg.logFile) return;
    try { fs.appendFileSync(this.cfg.logFile, `[${new Date().toISOString()}] ${text}\n`); } catch { /* logging is best effort */ }
  }

  async start() {
    this.stopping = false;
    if (await this.healthy(this.cfg.url)) {  // already running (started by you or by an earlier session): leave it
      this.set("running", "ROOKS was already running.");
      this.watch();
      return true;
    }
    this.set("starting", "Starting ROOKS…");
    if (!(await this.launch())) return false;
    if (await this.waitHealthy()) return true;
    if (this.state === "failed" || this.stopping) return false;
    return this.recover("ROOKS stopped while starting.");  // it exited before answering: treat it as a crash
  }

  async launch() {
    const c = this.cfg;
    if (c.strategy === "compose") {
      const r = await run("docker", ["compose", "up", "-d", "workbench"], { cwd: c.dir });
      this.log(`docker compose up: exit ${r.code}\n${r.out}`);
      if (r.code !== 0) {
        const why = /Cannot connect to the Docker daemon|docker daemon/i.test(r.out) ? "Docker is not running. Start Docker Desktop, then Retry."
          : r.code === -1 ? "Docker is not installed or not on the PATH." : `docker compose failed: ${r.out.trim().split("\n").slice(-3).join(" ")}`;
        this.fail(why);
        return false;
      }
      this.startedByUs = true;
      return true;
    }
    if (c.strategy === "process") {
      if (!c.command) { this.fail("No command to start ROOKS is set."); return false; }
      const out = c.logFile ? fs.openSync(c.logFile, "a") : "ignore";
      const child = spawn(c.command, c.args || [], { cwd: c.cwd || undefined, env: { ...process.env, ...(c.env || {}) },
        stdio: ["ignore", out, out], detached: process.platform !== "win32" });
      this.child = child;
      this.startedByUs = true;
      child.on("error", (e) => { this.log(`spawn error: ${e.message}`); });
      child.on("exit", (code, signal) => {
        this.log(`server exited: code ${code} signal ${signal}`);
        if (this.child === child) this.child = null;
        // While starting or restarting, the loop that launched it notices the exit itself
        if (!this.stopping && !this.recovering && this.state === "running") this.recover(`ROOKS stopped (${signal || `exit ${code}`}).`);
      });
      return true;
    }
    this.fail(`Unknown way to start ROOKS: ${c.strategy}`);
    return false;
  }

  async waitHealthy() {
    const end = Date.now() + this.startTimeout;
    while (Date.now() < end && !this.stopping) {
      if (await this.healthy(this.cfg.url)) {
        this.set("running", "");
        this.watch();
        return true;
      }
      if (this.cfg.strategy === "process" && !this.child) return false;  // exited before answering: the caller restarts it
      await new Promise((r) => setTimeout(r, 500));
    }
    if (!this.stopping) this.fail(`ROOKS did not answer at ${this.cfg.url} within ${Math.round(this.startTimeout / 1000)} seconds.`);
    return false;
  }

  watch() {
    clearInterval(this.timer);
    let misses = 0;
    this.timer = setInterval(async () => {
      if (this.state !== "running" || this.stopping) return;
      if (await this.healthy(this.cfg.url)) { misses = 0; return; }
      misses += 1;
      if (misses >= 2) this.recover("ROOKS stopped answering.");  // two misses in a row, not one slow answer
    }, this.checkEvery);
  }

  /** Restarts until ROOKS answers, gives up after too many failures, or stops when you quit. One loop at a time. */
  async recover(why) {
    if (this.stopping || this.recovering) return false;
    this.recovering = true;
    try {
      for (;;) {
        const now = Date.now();
        this.failures = this.failures.filter((t) => now - t < WINDOW_MS).concat(now);
        if (this.failures.length > this.backoff.length) {
          this.fail(`${why} It was restarted ${this.backoff.length} times in two minutes and keeps stopping. See the log, then Retry.`);
          return false;
        }
        const wait = this.backoff[this.failures.length - 1];
        this.set("restarting", `${why} Restarting in ${wait / 1000} s (attempt ${this.failures.length} of ${this.backoff.length}).`);
        await new Promise((r) => setTimeout(r, wait));
        if (this.stopping) return false;
        if (this.cfg.strategy === "process" && this.child) await this.killChild();
        if (!(await this.launch())) return false;
        if (await this.waitHealthy()) return true;
        if (this.state === "failed" || this.stopping) return false;
        why = "ROOKS stopped while starting.";
      }
    } finally {
      this.recovering = false;
    }
  }

  fail(why) {
    clearInterval(this.timer);
    this.set("failed", why);
  }

  killChild() {
    const child = this.child;
    if (!child) return Promise.resolve();
    return new Promise((resolve) => {
      const t = setTimeout(() => { try { this.signal(child, "SIGKILL"); } catch { /* gone */ } }, 5000);
      child.once("exit", () => { clearTimeout(t); resolve(); });
      try { this.signal(child, "SIGTERM"); } catch { clearTimeout(t); resolve(); }
    });
  }

  signal(child, sig) {
    if (process.platform !== "win32") process.kill(-child.pid, sig);  // the whole group: uvicorn and its workers
    else child.kill(sig);
  }

  /** On quit: stop only what this app started, and only when the settings say so. */
  async stop() {
    this.stopping = true;
    clearInterval(this.timer);
    if (!this.startedByUs || this.cfg.stopOnQuit === false) { this.set("stopped", "Left running."); return; }
    if (this.cfg.strategy === "process") await this.killChild();
    else if (this.cfg.strategy === "compose") this.log(`docker compose stop: ${(await run("docker", ["compose", "stop", "workbench"], { cwd: this.cfg.dir })).code}`);
    this.set("stopped", "");
  }

  async retry() {
    this.failures = [];
    return this.start();
  }
}

module.exports = { Supervisor, detectCompose, healthy, readEnvFile };
