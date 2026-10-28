"use strict";
/**
 * ROOKS desktop: the ROOKS web app in its own window, with the local server looked after (supervisor.js), desktop
 * notifications (live.js) and, if you choose, the ROOKS Companion so ROOKS can act on this computer.
 *
 * Security: the ROOKS page runs with context isolation, the sandbox and no Node access; it gets no preload at all.
 * Only the app's own setup page (a local file) has a small, fixed bridge. Navigation stays on the ROOKS server;
 * other links open in your browser. Permissions are refused except notifications, and the microphone for voice,
 * for the ROOKS server only. Nothing gives ROOKS access to a folder unless you pick it in a folder dialog.
 */
const { app, BrowserWindow, Menu, Notification, dialog, ipcMain, net, session, shell } = require("electron");
const os = require("node:os");
const path = require("node:path");
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const { Supervisor, detectCompose, healthy } = require("./supervisor");
const settingsStore = require("./settings");
const { Live } = require("./live");

const TEST = process.env.ROOKS_DESKTOP_TEST === "1";
if (process.env.ROOKS_DESKTOP_USER_DATA) app.setPath("userData", process.env.ROOKS_DESKTOP_USER_DATA);
const SETTINGS = path.join(app.getPath("userData"), "settings.json");
const LOG = path.join(app.getPath("userData"), "rooks-server.log");
const SETUP = path.join(__dirname, "setup.html");

let win = null;
let sup = null;
let live = null;
let companion = null;
let settings = settingsStore.load(SETTINGS);
let lastPath = "/";
const notes = [];  // what was shown, for the diagnostics and the tests

if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } });

function origin() {
  const u = settingsStore.serverUrl(settings);
  try { return u ? new URL(u).origin : ""; } catch { return ""; }
}

function onServer(url) {
  try { return !!origin() && new URL(url).origin === origin(); } catch { return false; }
}

function showSetup(view = "") {
  if (win) win.loadFile(SETUP, { hash: view });
}

function openServer(p = lastPath) {
  if (!win) return;
  win.loadURL(origin() + (p || "/"));
}

function pushState() {
  if (win && !onServer(win.webContents.getURL())) win.webContents.send("rooks:state", state());
}

function state() {
  const home = os.homedir();
  const guess = settings.local.dir || path.join(home, "ai-workspace");
  return { settings, detected: detectCompose(guess), guessDir: guess, server: settingsStore.serverUrl(settings),
    supervisor: sup ? { state: sup.state, detail: sup.detail } : null, log: LOG, platform: process.platform,
    companion: { running: !!(companion && companion.exitCode === null), dir: companionDir() }, version: app.getVersion() };
}

// Starting ROOKS (local) or reaching it (remote)
async function connect() {
  if (sup) await sup.stop();
  sup = null;
  if (live) live.close();
  live = null;
  if (!settings.mode) { showSetup("welcome"); return; }
  if (settings.mode === "remote") {
    showSetup("status");
    win.webContents.once("did-finish-load", () => win.webContents.send("rooks:state", { ...state(), supervisor: { state: "starting", detail: `Reaching ${settings.remoteUrl}…` } }));
    if (await healthy(settings.remoteUrl, 8000)) return ready();
    win.webContents.send("rooks:state", { ...state(), supervisor: { state: "failed", detail: `${settings.remoteUrl} did not answer. Check the address and your connection, then Retry.` } });
    return;
  }
  const local = { ...settings.local, stopOnQuit: settings.stopOnQuit, logFile: LOG };
  sup = new Supervisor(local);
  sup.on("state", (s) => {
    if (TEST) console.log(`SUPERVISOR ${JSON.stringify(s)}`);
    if (s.state === "running") ready();
    else if (win && onServer(win.webContents.getURL())) { lastPath = new URL(win.webContents.getURL()).pathname; showSetup("status"); }
    pushState();
  });
  showSetup("status");
  await sup.start();
}

function ready() {
  openServer(lastPath);
  if (!live) startLive();
}

function startLive() {
  live = new Live(net, win.webContents.session, origin());
  live.on("notify", (n) => {
    notes.push(n);
    if (TEST) console.log(`NOTIFY ${JSON.stringify(n)}`);
    if (!Notification.isSupported()) return;
    const note = new Notification({ title: n.title, body: n.body, silent: false });
    note.on("click", () => { win.show(); win.focus(); openServer(n.path); });  // opens the job or approval it is about
    note.show();
  });
  live.start();
}

// The ROOKS Companion: only when you turn it on; folders only when you pick them
function companionDir() {
  if (settings.companion.dir) return settings.companion.dir;
  const fromInstall = settings.local.dir ? path.join(settings.local.dir, "companion") : "";
  return fromInstall && fs.existsSync(path.join(fromInstall, "rooks_companion")) ? fromInstall : "";
}

function runCompanion(args) {
  return new Promise((resolve) => {
    const dir = companionDir();
    if (!dir) return resolve({ code: -1, out: "The ROOKS Companion was not found. It comes with the ROOKS install (the companion folder)." });
    let out = "";
    const p = spawn(settings.companion.python || "python3", ["-m", "rooks_companion", ...args], { cwd: dir, env: process.env });
    p.stdout.on("data", (d) => { out += d; });
    p.stderr.on("data", (d) => { out += d; });
    p.on("error", (e) => resolve({ code: -1, out: e.message }));
    p.on("close", (code) => resolve({ code, out: out.slice(-2000) }));
  });
}

async function pairCompanion() {
  const r = await win.webContents.session.fetch(`${origin()}/api/computer/pair`, { method: "POST", headers: { "X-Workbench": "1" } });
  if (!r.ok) return { ok: false, text: r.status === 401 ? "Sign in to ROOKS first." : `ROOKS refused pairing (${r.status}).` };
  const { code } = await r.json();
  const p = await runCompanion(["pair", origin(), code, "--name", os.hostname()]);
  if (p.code !== 0) return { ok: false, text: p.out.trim() || "Pairing failed." };
  settings.companion.enabled = true;
  settingsStore.save(SETTINGS, settings);
  startCompanion();
  return { ok: true, text: "This computer is paired. ROOKS asks before it does anything on it." };
}

function startCompanion() {
  if (!settings.companion.enabled || (companion && companion.exitCode === null)) return;
  const dir = companionDir();
  if (!dir) return;
  companion = spawn(settings.companion.python || "python3", ["-m", "rooks_companion", "run"], { cwd: dir, env: process.env, stdio: "ignore" });
  companion.on("exit", (code) => { if (TEST) console.log(`COMPANION exit ${code}`); });
}

async function allowFolder() {
  const pick = await dialog.showOpenDialog(win, { title: "Choose a folder ROOKS may work in", properties: ["openDirectory", "createDirectory"] });
  if (pick.canceled || !pick.filePaths[0]) return { ok: false, text: "No folder chosen; nothing changed." };
  const r = await runCompanion(["allow-folder", pick.filePaths[0]]);
  return { ok: r.code === 0, text: r.code === 0 ? `ROOKS may now work in ${pick.filePaths[0]} (it never deletes).` : r.out.trim() };
}

// The setup page's bridge (preload.js exposes exactly these). Each call must come from the app's own setup page.
function handle(name, fn) {
  ipcMain.handle(name, (e, ...args) => {
    if (!e.senderFrame || !e.senderFrame.url.startsWith("file:")) throw new Error("Not available to this page.");
    return fn(e, ...args);
  });
}
handle("rooks:get-state", () => state());
handle("rooks:save", async (_e, s) => {
  if (s.mode === "remote") {
    const problem = settingsStore.checkUrl(s.remoteUrl || "");
    if (problem) return { ok: false, text: problem };
    settings.mode = "remote";
    settings.remoteUrl = s.remoteUrl.replace(/\/+$/, "");
  } else if (s.mode === "local") {
    if (s.strategy === "compose") {
      const found = detectCompose(s.dir || "");
      if (!found) return { ok: false, text: "No ROOKS install in that folder (no docker-compose.yml). Install ROOKS first, or choose its folder." };
      settings.local = { ...settings.local, strategy: "compose", dir: found.dir, url: found.url };
    } else {
      const problem = settingsStore.checkUrl(s.url || "");
      if (problem) return { ok: false, text: problem };
      if (!s.command) return { ok: false, text: "Give the command that starts ROOKS." };
      settings.local = { ...settings.local, strategy: "process", url: s.url, command: s.command, args: s.args || [], cwd: s.cwd || "", env: s.env || {} };
    }
    settings.mode = "local";
    if (typeof s.stopOnQuit === "boolean") settings.stopOnQuit = s.stopOnQuit;
  } else return { ok: false, text: "Choose this computer or a ROOKS server." };
  settingsStore.save(SETTINGS, settings);
  connect();
  return { ok: true };
});
handle("rooks:retry", () => (sup ? sup.retry() : connect()));
handle("rooks:reset", () => { settings = settingsStore.load("/nonexistent"); settingsStore.save(SETTINGS, settings); return connect(); });
handle("rooks:choose-dir", async () => {
  const pick = await dialog.showOpenDialog(win, { title: "Choose your ROOKS install folder", properties: ["openDirectory"] });
  return pick.canceled ? "" : pick.filePaths[0];
});
handle("rooks:open-log", () => shell.openPath(LOG));
handle("rooks:pair", () => pairCompanion());
handle("rooks:allow-folder", () => allowFolder());
handle("rooks:open-server", () => { if (settingsStore.serverUrl(settings)) openServer(lastPath); });

function createWindow() {
  win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 380, minHeight: 520, title: "ROOKS", backgroundColor: "#faf9f6", show: !TEST || process.env.ROOKS_DESKTOP_SHOW === "1",
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, preload: path.join(__dirname, "preload.js"), spellcheck: true },
  });
  if (TEST) win.showInactive();
  const wc = win.webContents;
  wc.setWindowOpenHandler(({ url }) => {
    if (onServer(url)) { openServer(new URL(url).pathname + new URL(url).search); return { action: "deny" }; }
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  wc.on("will-navigate", (e, url) => {
    if (url.startsWith("file:") || onServer(url)) return;
    e.preventDefault();
    if (/^https?:/.test(url)) shell.openExternal(url);
  });
  wc.on("did-navigate-in-page", (_e, url) => { if (onServer(url)) lastPath = new URL(url).pathname; });
  wc.on("did-fail-load", (_e, code, desc, url) => {
    if (onServer(url) && code !== -3) { showSetup("status"); pushState(); }
  });
  wc.on("did-finish-load", () => pushState());
  createMenu();
}

function createMenu() {
  const go = (p) => () => { if (origin()) openServer(p); };
  const template = [
    { label: "ROOKS", submenu: [
      { label: "New chat", accelerator: "CmdOrCtrl+N", click: go("/") },
      { label: "Needs your review", click: go("/needs-you") },
      { label: "Settings", accelerator: "CmdOrCtrl+,", click: go("/settings") },
      { type: "separator" },
      { label: "Connection and this computer…", click: () => showSetup("status") },
      { type: "separator" },
      { role: "quit" },
    ] },
    { label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [{ role: "reload" }, { role: "zoomIn" }, { role: "zoomOut" }, { role: "resetZoom" }, { type: "separator" }, { role: "togglefullscreen" }, { role: "toggleDevTools" }] },
    { label: "Help", submenu: [
      { label: "Server log", click: () => shell.openPath(LOG) },
      { label: "About ROOKS", click: () => dialog.showMessageBox(win, { message: `ROOKS desktop ${app.getVersion()}`, detail: settingsStore.serverUrl(settings) || "Not set up yet." }) },
    ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  // Permissions: notifications and the microphone (voice), for the ROOKS server only; everything else is refused
  session.defaultSession.setPermissionRequestHandler((wc, permission, cb, details) => {
    const fromRooks = onServer(details.requestingUrl || wc.getURL());
    const audioOnly = permission === "media" && (details.mediaTypes || []).every((t) => t === "audio");
    cb(fromRooks && (permission === "notifications" || audioOnly));
  });
  session.defaultSession.setPermissionCheckHandler((wc, permission, requestingOrigin) => requestingOrigin === origin() && ["notifications", "media"].includes(permission));
  session.defaultSession.on("will-download", (_e, item) => {  // downloads always ask where to save
    item.setSaveDialogOptions({ title: "Save from ROOKS", defaultPath: path.join(app.getPath("downloads"), item.getFilename()) });
  });
  if (process.env.ROOKS_DESKTOP_SETTINGS && !fs.existsSync(SETTINGS)) {  // a prepared setup (tests, managed installs)
    settings = settingsStore.load(process.env.ROOKS_DESKTOP_SETTINGS);
    settingsStore.save(SETTINGS, settings);
  }
  createWindow();
  connect();
  startCompanion();
  if (TEST) global.rooksDesktop = { notes, state, supervisor: () => sup, live: () => live };
});

let quitting = false;
app.on("before-quit", (e) => {
  if (quitting) return;
  quitting = true;
  e.preventDefault();
  (async () => {
    if (live) live.close();
    if (companion && companion.exitCode === null) companion.kill("SIGTERM");
    if (sup) await sup.stop();
    app.exit(0);
  })();
});
app.on("window-all-closed", () => app.quit());
