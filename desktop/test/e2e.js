"use strict";
/**
 * The desktop app end to end, on Linux under Xvfb: the real Electron app starts a real ROOKS server (the "process" way,
 * uvicorn) with the stand-in AI (workbench/tests/browser/fake_ai.py, not a model), shows the same web app, keeps the
 * page away from Node and other sites, notifies when something needs review, restarts the server after it is killed,
 * pairs the ROOKS Companion only when asked, and stops what it started when it quits.
 *
 * Usage: ROOKS_PY=/path/to/python-with-backend-requirements xvfb-run -a node test/e2e.js
 * It needs Playwright for Node (require("playwright")).
 */
const { _electron: electron } = require("playwright");
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const http = require("node:http");

const ROOT = path.resolve(__dirname, "..", "..");
const PY = process.env.ROOKS_PY || "python3";
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `  (${typeof detail === "string" ? detail : JSON.stringify(detail)})`}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const freePort = () => new Promise((r) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); }); });
const until = async (fn, ms = 30000) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await fn()) return true; } catch { /* not yet */ } await sleep(250); } return false; };
const health = (url) => new Promise((resolve) => { http.get(`${url}/healthz`, (r) => { r.resume(); resolve(r.statusCode === 200); }).on("error", () => resolve(false)); });

function req(url, method, pathName, body, cookie) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const r = http.request(`${url}${pathName}`, { method, headers: { "X-Workbench": "1", "Content-Type": "application/json", ...(cookie ? { Cookie: cookie } : {}) } }, (res) => {
      let t = "";
      res.on("data", (d) => { t += d; });
      res.on("end", () => { let j = null; try { j = JSON.parse(t); } catch { j = t; } resolve({ status: res.statusCode, body: j, cookie: (res.headers["set-cookie"] || []).map((c) => c.split(";")[0]).join("; ") }); });
    });
    r.on("error", reject);
    if (data) r.write(data);
    r.end();
  });
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rooks-desktop-e2e-"));
  const aiPort = await freePort(), port = await freePort();
  const ai = spawn(PY, [path.join(ROOT, "workbench/tests/browser/fake_ai.py"), String(aiPort), path.join(tmp, "ai.jsonl")], { stdio: "ignore" });
  const url = `http://127.0.0.1:${port}`;
  const data = path.join(tmp, "data");
  fs.mkdirSync(data);
  const settingsFile = path.join(tmp, "seed.json");
  fs.writeFileSync(settingsFile, JSON.stringify({ mode: "local", stopOnQuit: true,
    local: { strategy: "process", url, command: PY, args: ["-m", "uvicorn", "app.main:app", "--port", String(port)], cwd: path.join(ROOT, "workbench/backend"),
      env: { APP_SECRET: "s".repeat(40), TOOLS_API_KEY: "t".repeat(30), DATA_DIR: data, STATIC_DIR: path.join(ROOT, "workbench/frontend/dist"), NO_PROXY: "127.0.0.1,localhost" } },
    companion: { enabled: false, python: PY, dir: path.join(ROOT, "companion") } }));
  const userData = path.join(tmp, "userdata");
  const env = { ...process.env, ROOKS_DESKTOP_TEST: "1", ROOKS_DESKTOP_USER_DATA: userData, ROOKS_DESKTOP_SETTINGS: settingsFile,
    ROOKS_COMPANION_HOME: path.join(tmp, "companion-home"), NO_PROXY: "127.0.0.1,localhost" };
  const app = await electron.launch({ executablePath: require(path.resolve(__dirname, "..", "node_modules", "electron")), args: ["--no-sandbox", path.resolve(__dirname, "..")], env });
  const logs = [];
  app.process().stdout.on("data", (d) => logs.push(String(d)));
  try {
    const win = await app.firstWindow();
    check("the app starts the ROOKS server it is set up for", await until(() => health(url), 90000));
    check("the window opens the ROOKS web app from that server", await until(() => win.url().startsWith(url), 30000), win.url());
    // First run of a new server: set it up through the API, then sign in in the window like a person
    const setup = await req(url, "POST", "/api/setup", { password: "desktop-pass-1" });
    await req(url, "PUT", "/api/ai-mode", { mode: "custom" }, setup.cookie);
    await req(url, "POST", "/api/providers/connect", { name: "Stand-in AI", base_url: `http://127.0.0.1:${aiPort}/v1`, api_key: "x" }, setup.cookie);
    await win.reload();
    await win.locator("#login-password").fill("desktop-pass-1");
    await win.getByRole("button", { name: "Sign in" }).click();
    await win.getByRole("heading", { name: "What can I help you get done?" }).waitFor({ timeout: 30000 });
    check("the same assistant as the browser, signed in", true);
    check("no microphone in the app: Electron has no speech service for the browser option",
      (await win.getByRole("button", { name: "Start a voice conversation" }).count()) === 0);
    await win.screenshot({ path: path.join(process.env.ROOKS_SHOTS || tmp, "desktop-home.png") });

    // The page gets no Node, no bridge, and cannot open other sites inside the app
    const isolation = await win.evaluate(() => ({ require: typeof window.require, process: typeof window.process, bridge: typeof window.rooks }));
    check("the ROOKS page has no Node access and no desktop bridge", isolation.require === "undefined" && isolation.process === "undefined" && isolation.bridge === "undefined", isolation);
    const before = app.windows().length;
    await win.evaluate(() => window.open("https://example.com/", "_blank"));
    await sleep(800);
    check("another site never opens inside the app (it goes to your browser)", app.windows().length === before && win.url().startsWith(url), app.windows().length);
    const perm = await win.evaluate(async () => { try { await navigator.mediaDevices.getUserMedia({ video: true }); return "granted"; } catch (e) { return e.name; } });
    check("the camera is refused (only notifications and the microphone are allowed, for ROOKS)", perm !== "granted", perm);

    // A conversation in the app
    await win.getByLabel("Message ROOKS").fill("How should I plan my week?");
    await win.keyboard.press("Enter");
    await win.getByText("Pick three outcomes").waitFor({ timeout: 30000 });
    check("asking in the app gets the answer", true);

    // Something needs review: a desktop notification that leads to it
    check("the app's live connection is up once you are signed in", await until(() => logs.join("").includes("LIVE event baseline"), 15000));
    const login = await req(url, "POST", "/api/login", { password: "desktop-pass-1" });
    await req(url, "POST", "/api/assistant/chat", { message: "Check in with me every weekday morning about deadlines" }, login.cookie);
    const noted = await until(() => app.evaluate(() => global.rooksDesktop.notes.some((n) => n.kind === "review")), 20000);
    const notes = await app.evaluate(() => global.rooksDesktop.notes);
    check("an approval waiting raises a desktop notification that opens Needs your review", noted && notes.find((n) => n.kind === "review").path === "/needs-you", notes);
    check("opening the app did not replay old news", notes.filter((n) => n.kind === "review").length === 1, notes);

    // The server is killed: the app restarts it and the window comes back
    const pid = await app.evaluate(() => global.rooksDesktop.supervisor().child.pid);
    process.kill(-pid, "SIGKILL");
    check("the app notices the server stopped", await until(() => logs.join("").includes('"state":"restarting"'), 20000));
    check("and restarts it", await until(() => health(url), 60000));
    check("the window returns to ROOKS by itself", await until(async () => win.url().startsWith(url) && (await win.getByRole("heading", { name: /What can I help|How should I plan/ }).count()) > 0, 60000), win.url());
    const conv = await req(url, "GET", "/api/conversations", null, login.cookie);
    check("nothing was lost: the conversation is still there after the restart", Array.isArray(conv.body) && conv.body.some((c) => /plan my week/i.test(c.title)), conv.status);

    // The setup page, and pairing the ROOKS Companion only when asked
    const file = path.resolve(__dirname, "..", "src", "setup.html");
    await app.evaluate(({ BrowserWindow }, f) => BrowserWindow.getAllWindows()[0].loadFile(f, { hash: "status" }), file);
    await win.waitForURL(/setup\.html#status/, { timeout: 10000 });
    await win.getByRole("status").first().waitFor();
    check("the status page says ROOKS is running", (await win.locator("#status-text").innerText()).includes("running") || (await win.locator("#status-text").innerText()) === "", await win.locator("#status-text").innerText());
    const devicesBefore = await req(url, "GET", "/api/computer", null, login.cookie);
    check("no computer is paired until you ask", (devicesBefore.body.devices || []).length === 0, devicesBefore.body);
    await win.getByRole("button", { name: "Let ROOKS use this computer…" }).click();
    await win.locator("#companion-msg").filter({ hasText: /paired|failed|refused|not found|Sign in/i }).waitFor({ timeout: 60000 });
    const msg = await win.locator("#companion-msg").innerText();
    check("pairing from the app pairs this computer", /paired/i.test(msg), msg);
    const online = await until(async () => ((await req(url, "GET", "/api/computer", null, login.cookie)).body.devices || []).some((d) => d.online), 45000);
    check("and the ROOKS Companion it started reports in", online);
    await win.screenshot({ path: path.join(process.env.ROOKS_SHOTS || tmp, "desktop-status.png") });
  } catch (e) {
    check(`no unexpected error: ${e.message.split("\n")[0]}`, false, e.stack);
  } finally {
    await app.close().catch(() => {});
    check("quitting stops the server the app started", await until(async () => !(await health(url)), 20000));
    await sleep(500);
    const leftover = execFileSync("ps", ["-eo", "args"]).toString().split("\n").filter((l) => l.includes("rooks_companion run") && !l.includes("grep"));
    check("and the companion it started", leftover.length === 0, leftover);
    ai.kill();
  }
  if (process.env.E2E_DEBUG) console.log(logs.join("").split("\n").filter((l) => /LIVE|NOTIFY/.test(l)).slice(0, 40).join("\n"));
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed.`);
  process.exit(passed === results.length ? 0 : 1);
})();
