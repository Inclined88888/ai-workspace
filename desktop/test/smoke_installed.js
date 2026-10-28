"use strict";
/**
 * Installer smoke test: the INSTALLED app (from the .deb or an unpacked AppImage), not the source checkout.
 *   1. First run with no settings: the setup screen asks where ROOKS is, and refuses an unencrypted remote address.
 *   2. Set up to run a local ROOKS server: it starts the server, opens it, and stops it on quit.
 *
 * Usage: ROOKS_APP=/usr/bin/rooks-desktop ROOKS_PY=... xvfb-run -a node test/smoke_installed.js
 */
const { _electron: electron } = require("playwright");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const http = require("node:http");

const APP = process.env.ROOKS_APP || "/usr/bin/rooks-desktop";
const ROOT = path.resolve(__dirname, "..", "..");
const PY = process.env.ROOKS_PY || "python3";
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `  (${detail})`}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const freePort = () => new Promise((r) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); }); });
const health = (url) => new Promise((resolve) => { http.get(`${url}/healthz`, (r) => { r.resume(); resolve(r.statusCode === 200); }).on("error", () => resolve(false)); });
const until = async (fn, ms = 30000) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await fn()) return true; } catch { /* not yet */ } await sleep(250); } return false; };

(async () => {
  check(`the installed app exists at ${APP}`, fs.existsSync(APP));
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rooks-smoke-"));
  const base = { ...process.env, ROOKS_DESKTOP_TEST: "1", NO_PROXY: "127.0.0.1,localhost" };

  // 1. First run
  let app = await electron.launch({ executablePath: APP, args: ["--no-sandbox"], env: { ...base, ROOKS_DESKTOP_USER_DATA: path.join(tmp, "first") } });
  try {
    const win = await app.firstWindow();
    await win.getByRole("heading", { name: "Where is your ROOKS?" }).waitFor({ timeout: 30000 });
    check("first run: the setup screen asks where ROOKS is", true);
    await win.getByRole("radio", { name: /A ROOKS server/ }).check();
    await win.getByLabel("Server address").fill("http://192.168.1.20:3100");
    await win.getByRole("button", { name: "Continue" }).click();
    const err = await win.getByRole("alert").innerText();
    check("an unencrypted address on another computer is refused, with why", /must use https/.test(err), err);
    await win.screenshot({ path: path.join(process.env.ROOKS_SHOTS || tmp, "desktop-setup.png") });
  } finally {
    await app.close().catch(() => {});
  }

  // 2. Local server, started and stopped by the installed app
  const port = await freePort();
  const url = `http://127.0.0.1:${port}`;
  const data = path.join(tmp, "data");
  fs.mkdirSync(data);
  const seed = path.join(tmp, "seed.json");
  fs.writeFileSync(seed, JSON.stringify({ mode: "local", stopOnQuit: true, local: { strategy: "process", url, command: PY,
    args: ["-m", "uvicorn", "app.main:app", "--port", String(port)], cwd: path.join(ROOT, "workbench/backend"),
    env: { APP_SECRET: "s".repeat(40), TOOLS_API_KEY: "t".repeat(30), DATA_DIR: data, STATIC_DIR: path.join(ROOT, "workbench/frontend/dist") } } }));
  app = await electron.launch({ executablePath: APP, args: ["--no-sandbox"], env: { ...base, ROOKS_DESKTOP_USER_DATA: path.join(tmp, "second"), ROOKS_DESKTOP_SETTINGS: seed } });
  try {
    const win = await app.firstWindow();
    check("the installed app starts the local server", await until(() => health(url), 90000));
    check("and opens ROOKS in its window", await until(() => win.url().startsWith(url), 30000), win.url());
    await win.getByRole("button", { name: "Get started" }).waitFor({ timeout: 30000 });
    check("a new ROOKS shows its first-run screen inside the app", true);
    const version = await app.evaluate(({ app: a }) => a.getVersion());
    check("it is the packaged version 1.0.0", version === "1.0.0", version);
  } finally {
    await app.close().catch(() => {});
  }
  check("quitting the installed app stops the server it started", await until(async () => !(await health(url)), 20000));
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed.`);
  process.exit(passed === results.length ? 0 : 1);
})();
