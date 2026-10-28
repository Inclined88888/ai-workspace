"use strict";
// The setup page's bridge, and nothing else. On a ROOKS page this exposes nothing: the page is not a local file.
const { contextBridge, ipcRenderer } = require("electron");

if (location.protocol === "file:") {
  contextBridge.exposeInMainWorld("rooks", {
    getState: () => ipcRenderer.invoke("rooks:get-state"),
    save: (s) => ipcRenderer.invoke("rooks:save", s),
    retry: () => ipcRenderer.invoke("rooks:retry"),
    reset: () => ipcRenderer.invoke("rooks:reset"),
    chooseDir: () => ipcRenderer.invoke("rooks:choose-dir"),
    openLog: () => ipcRenderer.invoke("rooks:open-log"),
    pair: () => ipcRenderer.invoke("rooks:pair"),
    allowFolder: () => ipcRenderer.invoke("rooks:allow-folder"),
    openServer: () => ipcRenderer.invoke("rooks:open-server"),
    onState: (fn) => ipcRenderer.on("rooks:state", (_e, s) => fn(s)),
  });
}
