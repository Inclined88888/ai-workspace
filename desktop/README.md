# AI Workspace desktop app

Tauri v2 shell that turns Workbench into a double-click app for macOS, Windows (and Linux).

```
desktop/
  ui/index.html              splash shown while the backend starts (bootStatus() is called from Rust)
  sidecar/workbench_server.py  entry point of the frozen backend: keychain secrets, per-user data dir, 127.0.0.1 only
  sidecar/workbench-server.spec  PyInstaller spec (bundles workbench/backend + workbench/frontend/dist)
  scripts/build-sidecar.mjs  freeze + copy to src-tauri/binaries/workbench-server-<target triple>
  scripts/smoke_sidecar.py   CI smoke test of the frozen backend
  src-tauri/
    src/sidecars.rs          sidecar registry + supervisor (start, health check, logs, kill on exit)
    src/approvals.rs         native approve / deny prompt (request_approval) + approval-requested event
    src/commands.rs          app_info, open_data_dir, open_logs
    src/updater.rs           in-app updater, compiled in only with --features updater
    capabilities/default.json  which commands the Workbench page (http://127.0.0.1:*) may call
    tauri.conf.json          bundle config (dmg, msi, nsis, deb, appimage)
    tauri.updater.conf.json  overlay used when the updater is turned on
```

## How it starts

1. The window opens on the splash (`ui/index.html`).
2. `sidecars::start_workbench` picks a free port, makes a random launch token and runs
   `workbench-server --port P --data-dir <app data dir> --exit-with-parent` with `WORKBENCH_DESKTOP_TOKEN`.
3. The sidecar loads or creates `APP_SECRET` / `TOOLS_API_KEY` in the OS keychain and serves Workbench with
   `WORKBENCH_DESKTOP=1` and `WORKBENCH_SECRET_BACKEND=keychain`.
4. When `/healthz` answers, the window navigates to `/api/desktop/session?token=…`, which sets the session cookie and
   redirects to `/welcome` (first run) or `/`.
5. On quit the shell kills the sidecar; if the shell crashes, the sidecar sees its stdin close and exits by itself.

Logs: `<app log dir>/workbench.log` (previous launch in `workbench.log.1`). Settings → Desktop app has buttons for both
folders.

## Extension points for the next phases

* **New sidecars** (phase 2 terminal coding agent, phase 3 AI browser agent, phase 4 computer use / MCP): add the
  frozen binary to `bundle.externalBin`, flip `enabled` in `SIDECARS` (`src/sidecars.rs`), and start it like
  `start_workbench` does. Settings → Desktop app already lists them as "coming soon".
* **Approvals**: anything that acts on the user's behalf calls `approvals::ask` (Rust) or
  `requestApproval()` from `workbench/frontend/src/desktop.ts`. Workbench's own held tool calls keep using its run log.
* **Settings for keys and connectors**: provider keys go through the existing `/api/providers` endpoints, which store
  them in the keychain in desktop mode. New connectors should use `security.store_connection_secret` the same way.
* **New native commands**: add the function, list it in `build.rs` (`AppManifest::commands`) and in
  `capabilities/default.json` (`allow-<name>`), and wrap it in `desktop.ts`.

## Build

See [docs/desktop-release.md](../docs/desktop-release.md#building-locally).
