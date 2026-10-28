# ROOKS desktop

The ROOKS web app in its own window, with the local ROOKS server looked after, desktop notifications, and the ROOKS
Companion when you choose to let ROOKS act on your computer. Source in `desktop/`.

## Why Electron

| | Electron | Tauri | Platform-native (for example pywebview) |
|---|---|---|---|
| Reuses the React web app | yes, unchanged | yes | yes |
| Rendering engine | Chromium, the same engine every ROOKS browser test runs on (6 suites, 485 screen checks) | the system WebView: WebKitGTK on Linux, WKWebView on macOS, WebView2 on Windows; three engines to test | system WebView, as Tauri |
| Size | about 116 MB AppImage, 81 MB .deb (measured below) | typically under 15 MB | small, but a Python runtime has to ship |
| Memory | higher: a Chromium process tree | lower | lower |
| Updates | electron-updater needs signed builds and a release feed | built-in updater with signed manifests | none built in |
| Build here | built and tested (below) | needs WebKitGTK development packages, not installed on the build machine | not tried |
| Backend language | unchanged (the app starts the Python server or Docker) | unchanged (sidecar) | unchanged |
| Precedent | DeepSeek Harness ships an Electron desktop around its web app (`docs/DSH_EVALUATION.md`) | | |

Decision: **Electron**, because the desktop window then renders exactly what the browser tests verify, and it could be
built and tested here. The cost is size and memory. Tauri is the option to revisit if size matters more than one
tested engine.

## What it does

- **First run:** asks where ROOKS is: on this computer (the standard Docker install, found from its folder and `.env`)
  or a ROOKS server at an https address. An http address is accepted only for this computer.
- **Looks after a local server:** starts it (`docker compose up -d workbench`, or a command you configure), checks it
  every 3 seconds, restarts it with a growing wait (1, 2, 4, 8, 16 seconds) after it stops, and gives up with the
  reason after 5 failures in two minutes. On quit it stops only what it started, and only if "Stop ROOKS when I quit"
  is on. A server that was already running is used and left running.
- **Recovery:** while the server is down the window shows what is happening; when it answers again the window goes
  back to where you were.
- **Notifications:** from the server's live events (`/api/v1/events`) with your signed-in session: something needs
  your review, work is ready for review, completed or could not finish. Clicking one opens the conversation or Needs
  your review. Opening the app does not replay old news.
- **This computer:** "Let ROOKS use this computer…" pairs the ROOKS Companion with a code from your server and starts
  it; "Allow a folder…" adds a folder you pick in the system dialog. Nothing is paired or allowed otherwise. The
  companion asks before each action (see `docs/COMPUTER_USE.md`).
- **Files:** attach with the normal file picker; downloads always ask where to save.
- **Start menu:** the .deb installs `ROOKS` (category Office) and `/usr/bin/rooks-desktop`.

## Security

- The ROOKS page runs with context isolation, the sandbox and no Node.js; `window.require`, `window.process` and the
  setup bridge are undefined there (tested).
- The app's own setup page is a local file with a strict Content Security Policy; its bridge has nine fixed calls, and
  each call is refused unless it comes from that local page.
- Navigation stays on your ROOKS server; other links and `window.open` go to your browser (tested).
- Permissions: notifications, and the microphone for voice, for the ROOKS server only; everything else is refused
  (camera tested).
- Settings are kept in the app's data folder with mode 0600. The app never stores your ROOKS password; the session is
  the same cookie a browser keeps.

## Tested (Linux x64, Ubuntu 24.04 container, Xvfb)

| Test | What | Result |
|---|---|---|
| `npm test` (`test/supervisor.test.js`, `test/settings.test.js`) | start, crash and restart, crash loop given up, never healthy, already running left alone, stop on quit off, `.env` address, no Docker, https rule, private settings | 10/10 |
| `npm run test:e2e` (`test/e2e.js`), source checkout | the app starts a real ROOKS server and the stand-in AI, sign in, same assistant, page isolation, external link, camera refused, a reply, a notification for a new approval, server killed and restarted with the window back and nothing lost, companion pairing from the app and the companion online, no microphone in the app (Electron has no speech service), quit stops server and companion | 21/21 |
| `test/smoke_installed.js` against the installed .deb (`/usr/bin/rooks-desktop`) | first-run setup, https rule, starts and opens a local server, packaged version, quit stops it | 8/8 |
| the same against the AppImage's contents (`--appimage-extract`) | same | 8/8 |

## Not tested, and what is needed

- **Windows and macOS: not built and not run.** Build on each platform: `npm ci && npx electron-builder --win nsis` on
  Windows, `npx electron-builder --mac dmg` on macOS. Then run `test/smoke_installed.js` with `ROOKS_APP` set to the
  installed executable, and check notifications, the folder dialog and the start menu or Dock by hand.
- **Code signing: none.** Windows SmartScreen and macOS Gatekeeper will warn about the unsigned builds. Signing needs a
  Windows code signing certificate and an Apple Developer ID with notarization; both cost money and were not set up.
- **Automatic updates: not implemented.** Safe updates need signed builds and a release feed; until then, install a new
  version over the old one. Settings and the ROOKS server's data are kept.
- **The Docker way of starting ROOKS** (`compose`) was tested only for its errors (no Docker); a full start needs Docker
  running and the ROOKS images built, which this machine did not have running.
- **Native notifications on a real desktop:** the notification logic was tested; a notification daemon showing it was
  not.

## Build

```sh
cd desktop
npm ci
npm test                        # unit tests
xvfb-run -a npm run test:e2e    # needs ROOKS_PY: a Python with workbench/backend/requirements.txt
npm run dist:linux              # dist/ROOKS-1.0.0.AppImage and dist/rooks-desktop_1.0.0_amd64.deb
```

Builds made for this release (not committed; rebuild with the command above):

| File | Size | SHA-256 |
|---|---|---|
| `ROOKS-1.0.0.AppImage` | 116,461,005 bytes | `9ec825738d2c6aed2188f3af43029d6af358cc3e0b336da8b9c168454ba4dee8` |
| `rooks-desktop_1.0.0_amd64.deb` | 80,637,046 bytes | `02c088e495c566b9314a79acbebd85c753e79f6eff691ceec4441b800044df08` |

Electron builds are not byte-for-byte reproducible, so a rebuild will have a different hash.
