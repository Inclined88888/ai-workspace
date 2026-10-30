# Desktop app: progress (phase 1 of 4)

Branch: `feat/desktop-app`. Last updated 2026-10-30 (AEST, UTC+10).

## Done

- [x] Backend desktop mode (`WORKBENCH_DESKTOP=1`): launch-token session (`/api/desktop/session`), welcome flag,
      `/api/desktop/info`, provider keys in the OS keychain (`keychain:connection:<id>` refs). Docker path unchanged.
- [x] Frontend: first-run Welcome (API key for any key-based provider, or local Ollama), desktop bridge
      (`src/desktop.ts`), Settings → Desktop app card. Password card and Sign out hidden on desktop.
- [x] Sidecar: `desktop/sidecar/workbench_server.py` + PyInstaller spec (one-file, ~53 MB on Linux), master secrets in
      the keychain with a 0600 file fallback, keychain probe with a timeout, exits when the shell dies.
- [x] Tauri v2 shell: splash, sidecar supervisor (free port, random token, health check, logs, kill on exit),
      single instance, native approval prompt, sidecar registry for phases 2–4, updater behind a Cargo feature (off).
- [x] `.github/workflows/desktop.yml`: dmg (arm64 + x64), msi + exe, deb + AppImage; signing/notarisation/updater
      auto-enable from secrets; release upload on tags.
- [x] Docs: `docs/desktop-release.md` (every secret + cost), `desktop/README.md`, README download section, CHANGELOG.
- [x] MIT LICENSE (**owner to confirm**).

## Verified on Linux (x86_64, this box)

- Backend: 167 passed (161 before + 6 new desktop tests). CLI tests: 3 passed. cli-launcher node tests: 14 passed.
  `bash -n setup.sh` ok. Frontend `tsc --noEmit` + `vite build` ok.
- Frozen sidecar smoke test (`desktop/scripts/smoke_sidecar.py`): health, wrong token 403, token → /welcome + cookie,
  frontend served, exits on stdin close.
- `npx tauri build --bundles deb,appimage` (release): `AI Workspace_0.1.0_amd64.deb` and `.AppImage` (156 MiB) built.
- Ran the release binary on X11: welcome flow, API-key step, Ollama step ("Not found" state), home after welcome,
  Settings → Desktop app (bridge: app_info, list_sidecars), native approval dialog round-trip (Deny → "Denied").
  Quitting the app stopped the sidecar. Launch token is `<redacted>` in workbench.log.
- Keychain: real Secret Service (gnome-keyring, unlocked) → `os-keychain`, no secrets file written, reused on second
  run. Locked keyring with no prompter → falls back to the file after the probe timeout instead of hanging.
- `cargo clippy` (default and `--features updater`): clean.

## Not verified (needs CI runners or real machines)

- macOS and Windows builds (first run of `desktop.yml`), dmg/msi/nsis installers, Intel vs Apple-silicon sidecars.
- Signing and notarisation (no certificates yet); hardened runtime with the one-file PyInstaller sidecar.
- macOS Keychain / Windows Credential Manager behaviour (only Linux Secret Service tested).
- Updater end to end (`latest.json` generation is not written yet).
- A real provider key through the Welcome flow (no key used here); Ollama "running" path (no Ollama on the box).
- browser-agent tests were not run locally (package not installed in this venv; code untouched).

## Next phases

2. Terminal coding agent sidecar · 3. AI browser agent sidecar · 4. Computer use / MCP layer. See `desktop/README.md`
   → "Extension points".
