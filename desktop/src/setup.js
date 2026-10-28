"use strict";
/* The desktop app's own page: choose where ROOKS is, see whether it is running, and pair this computer. It talks to the
   app only through window.rooks (preload.js). */
const $ = (id) => document.getElementById(id);
const WORDS = { starting: "Starting ROOKS…", running: "ROOKS is running.", restarting: "Restarting ROOKS…", failed: "ROOKS could not start.", stopped: "ROOKS is stopped." };

function show(view) {
  for (const id of ["welcome", "status"]) $(id).hidden = id !== view;
}

function render(s) {
  if (!s) return;
  const view = location.hash.replace("#", "") || (s.settings.mode ? "status" : "welcome");
  show(view);
  if (view === "welcome") {
    if (!$("dir").value) $("dir").value = s.settings.local.dir || s.guessDir;
    $("detected").textContent = s.detected ? `Found a ROOKS install. It will be at ${s.detected.url}.` : "No ROOKS install found in this folder.";
    if (s.settings.remoteUrl && !$("url").value) $("url").value = s.settings.remoteUrl;
    return;
  }
  const sup = s.supervisor || { state: s.server ? "running" : "stopped", detail: "" };
  $("status-text").textContent = sup.detail || WORDS[sup.state] || sup.state;
  $("status-text").className = `status ${sup.state}`;
  $("retry").hidden = sup.state !== "failed";
  $("open").hidden = sup.state !== "running";
  $("companion-state").textContent = s.companion.running ? "The ROOKS Companion is running on this computer."
    : s.companion.dir ? "The ROOKS Companion is not running. Pair it to let ROOKS act here." : "The ROOKS Companion was not found (it comes with the ROOKS install).";
  $("pair").disabled = !s.companion.dir || sup.state !== "running";
  $("folder").disabled = !s.companion.dir;
}

async function refresh() { render(await window.rooks.getState()); }

document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => {
  const local = document.querySelector('input[name="mode"]:checked').value === "local";
  $("local-fields").hidden = !local;
  $("remote-fields").hidden = local;
}));
$("choose").addEventListener("click", async () => { const d = await window.rooks.chooseDir(); if (d) { $("dir").value = d; } });
$("go").addEventListener("click", async () => {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  const r = await window.rooks.save(mode === "local" ? { mode, strategy: "compose", dir: $("dir").value.trim(), stopOnQuit: $("stop").checked }
    : { mode, remoteUrl: $("url").value.trim() });
  $("error").hidden = r.ok;
  $("error").textContent = r.ok ? "" : r.text;
  if (r.ok) { location.hash = "status"; refresh(); }
});
$("retry").addEventListener("click", () => window.rooks.retry());
$("open").addEventListener("click", () => window.rooks.openServer());
$("log").addEventListener("click", () => window.rooks.openLog());
$("change").addEventListener("click", () => { location.hash = "welcome"; refresh(); });
$("pair").addEventListener("click", async () => { $("companion-msg").textContent = "Pairing…"; const r = await window.rooks.pair(); $("companion-msg").textContent = r.text; refresh(); });
$("folder").addEventListener("click", async () => { const r = await window.rooks.allowFolder(); $("companion-msg").textContent = r.text; });
window.addEventListener("hashchange", refresh);
window.rooks.onState(render);
refresh();
