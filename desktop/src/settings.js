"use strict";
/**
 * The desktop app's own settings (not ROOKS's): where ROOKS is and how to start it. Kept in the app's data folder.
 *
 *   { mode: "local" | "remote", remoteUrl, local: { strategy, dir, url, command, args, cwd, env }, stopOnQuit,
 *     companion: { enabled, python, dir } }
 *
 * A remote server must use https unless it is on this computer: a token or session sent over plain http on a network
 * can be read by anyone on that network.
 */
const fs = require("node:fs");
const path = require("node:path");

const DEFAULTS = { mode: "", remoteUrl: "", local: { strategy: "compose", dir: "", url: "", command: "", args: [], cwd: "", env: {} },
  stopOnQuit: true, companion: { enabled: false, python: "python3", dir: "" } };

function isLoopback(u) {
  return ["localhost", "127.0.0.1", "[::1]", "::1"].includes(u.hostname);
}

/** Returns "" when the address is acceptable, else what is wrong with it. */
function checkUrl(raw) {
  let u;
  try { u = new URL(raw); } catch { return "That is not a web address. Example: https://rooks.example.com"; }
  if (!["http:", "https:"].includes(u.protocol)) return "Use an http or https address.";
  if (u.protocol === "http:" && !isLoopback(u)) return "A ROOKS server on another computer must use https, so your session cannot be read on the network.";
  if (u.username || u.password) return "Do not put a name or password in the address.";
  return "";
}

function load(file) {
  try {
    const s = JSON.parse(fs.readFileSync(file, "utf8"));
    return { ...DEFAULTS, ...s, local: { ...DEFAULTS.local, ...(s.local || {}) }, companion: { ...DEFAULTS.companion, ...(s.companion || {}) } };
  } catch {
    return JSON.parse(JSON.stringify(DEFAULTS));
  }
}

function save(file, s) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(s, null, 1), { mode: 0o600 });
  fs.renameSync(tmp, file);
}

/** The address the window should open, or "" when the app is not set up yet. */
function serverUrl(s) {
  if (s.mode === "remote") return s.remoteUrl.replace(/\/+$/, "");
  if (s.mode === "local") return (s.local.url || "").replace(/\/+$/, "");
  return "";
}

module.exports = { DEFAULTS, checkUrl, isLoopback, load, save, serverUrl };
