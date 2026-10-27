# Computer Use

ROOKS can use apps, its own browser and folders you allow on your own computer, through the optional **ROOKS
Companion**. It is off by default.

ROOKS itself never needs the companion. Without it, nothing on your computer can be touched.

## How it works

```
 Your computer                                     ROOKS (Docker, usually the same computer)
 ┌─────────────────────────────┐   outbound only   ┌───────────────────────────────────────┐
 │ ROOKS Companion             │ ────────────────▶ │ /api/companion/*  (device token)      │
 │  engine: checks + journal   │ ◀── one action ── │ computer.py: rules, queue, approvals, │
 │  adapters: macOS │ X11 │ Win │                   │   stop, recovery, activity log        │
 │  browser: Playwright        │                   │ assistant + missions: computer tools  │
 │  files: allowed folders     │                   │ Computer page + live Stop bar         │
 └─────────────────────────────┘                   └───────────────────────────────────────┘
```

**The companion opens no port.** It connects out to ROOKS with a device token from pairing and asks for one action
at a time. The companion itself exposes no listening port. ROOKS **does** expose authenticated
`/api/companion/*` routes on the ROOKS server. Do not publicly expose these routes. Pairing codes and
device tokens provide authentication; private-looking source IPs are not proof that a connection is local
when Docker forwarding, NAT or reverse proxies are involved.

## Three ways to get something done, cheapest first

The assistant is told to choose in this order. The tools are offered in this order too.

1. **Direct tools.** Notes, tasks, calendar, email drafts, and `files_*` for folders you allowed.
2. **ROOKS's browser** (`browser_*`). This is a separate Chromium profile, driven by Playwright. It never sees your
   own browser's cookies or passwords.
3. **Desktop apps** (`computer_*`). Accessibility first: elements are found and pressed through the accessibility
   API. Clicking at screen coordinates is off unless you enable it. **Optional visual targeting** may
  send an approved screenshot to the specifically selected vision model after explicit consent.

Falling back to another method never gets around a refusal. Every method passes the same rules.

## Choices and why

| Need | Chosen | Licence | Why |
|---|---|---|---|
| macOS desktop | Built-in: `osascript` (JavaScript for Automation with System Events), `screencapture`, `sips`, plus `AXIsProcessTrusted`, `CGPreflightScreenCaptureAccess`, `CGRequestScreenCaptureAccess` and `CGEventCreateScrollWheelEvent2` through `ctypes` | Apple system | Nothing to install. It uses the real accessibility tree, and macOS's own permission prompts apply. |
| Browser | Playwright for Python (optional) | Apache-2.0 | It is maintained and stable. Its own profile keeps it isolated from your personal browser. |
| Linux X11 | python-xlib with XTEST (optional) | LGPL-2.1+ | It provides real input on a real display, which is how the engine is tested here. X11 has no accessibility tree, so app elements are available only from apps that publish them. |
| Windows | Contract only (`adapters/windows.py`) | — | It maps onto Microsoft UI Automation. It is not implemented and is reported as unavailable. |

**Not chosen:**

- **PyObjC.** Larger, and everything needed is reachable through `osascript` and `ctypes`.
- **Browser Use.** It advertises CAPTCHA handling and "stealth", which ROOKS will not do.
- **pyautogui-style screen-coordinate automation as the main method.** It breaks when layouts move, and it clicks
  blindly.

## Safety

These rules are enforced on the server in `computer.py`, and again on the computer in the companion's
`policy.py` and `engine.py`:

- **Off by default.** The global switch is on the Computer page. Modes are *Look only*, *Ask before changes*
  (approval, or a temporary per-app grant), and *Allowed*.
- **Per-app rules.** Each app can be set to Never, Look only, or May change.
- **Never controlled, whatever the settings:** shells (Terminal, iTerm and others), password managers, Keychain
  Access, System Settings, Script Editor, Automator and Shortcuts. There is no route to arbitrary commands.
- **Always needs approval, whatever the mode:**
  - clicks on things labelled send, submit, buy, pay, delete, install, publish, confirm and similar;
  - Enter in messaging apps;
  - overwriting a file.

  The companion re-checks the label of the element actually on screen. A request claiming "Add one" for a
  "Delete all" button is refused.
- **Password fields** are never typed into, on the desktop or in the browser. Their values are never read.
- **Element ids expire after every change.** A click also re-checks the element's label, so a moved layout means
  observing again instead of clicking the wrong thing.
- **Screen and page text is data, never instructions.** After the conversation has read web pages or email, desktop
  and file changes need approval or a temporary grant.
- **Stop**, from the Computer page, the bar shown on every page while ROOKS acts, or `rooks_companion stop` on the
  computer:
  - cancels queued actions;
  - aborts the running one mid-way (typing, scrolling and waiting check every few characters or steps);
  - pauses missions;
  - refuses everything until you press *Allow again*.

  *Take control* is the same thing, resumable with one click.
- **Not for unattended runs.** Schedules, company agents and Telegram cannot use the computer.
- **After a restart:**
  - An action that was sent but never confirmed becomes *unconfirmed*.
  - Repeating the identical action, from any conversation, is refused until the screen has been observed again.
  - The companion journals every result before reporting it, and never runs the same action id twice.
  - Results it finished while ROOKS was down are reported when it reconnects.
- **Privacy:**
  - The activity log and tool log record what was done, never what was typed.
  - Screen previews are cached in server memory for up to 10 minutes, never intentionally written to
    the Computer Use action database. When visual targeting is enabled, an approved screenshot can be
    sent to the selected vision model; cloud sharing requires explicit consent and is blocked for
    local-only projects. Do not enable it around confidential windows without understanding the destination.
  - New action results are held transiently for active callers; database action records retain only
    status and minimal non-content metadata. Pending desktop approval payloads are encrypted at rest.
    Earlier ROOKS versions may have stored raw content; see the opt-in legacy cleanup section below.
  - The pairing token is saved with mode 600 in a folder with mode 700.
- **Network:**
  - ROOKS rejects connections with obviously public peer addresses unless remote companions are enabled,
    but Docker port forwarding and reverse proxies may make remote peers appear private. This check
    is only defense-in-depth, **not** a trustworthy network isolation boundary. Restrict the
    ROOKS server's exposure at your firewall/reverse proxy and use HTTPS if it crosses machines.
  - Pairing codes work once, for 10 minutes, and are rate limited.

## macOS permissions

macOS grants these to the app that runs the companion (Terminal, or Python). Run
`python3 -m rooks_companion permissions` to open the right panes, then `python3 -m rooks_companion check`.

| Permission | Needed for | Without it |
|---|---|---|
| Accessibility | Reading and using apps | Desktop control is unavailable; the reason is shown on the Computer page |
| Screen Recording | Screenshots for your live preview and visual targeting | Everything else works; visual targeting is unavailable |
| Automation (per app, asked by macOS) | Menus and window control through System Events | That app's menus are unavailable; the error names the app |

When a permission is turned off, the next action fails with that reason. The companion re-reports its permissions
every minute.

## Visual targeting (optional, off by default)

Some apps do not expose a control to accessibility. Visual targeting lets ROOKS look at a screenshot of **that one
window** and find it. It runs only when all of these hold:

1. You chose a vision model on the Computer page and it passed the image test. ROOKS sends it a small generated
   image (left half red, right half blue) and it must answer "red". A model that cannot see is never used, and no
   other model is ever tried instead.
2. For a model outside your computer or network, you agreed to send screenshots to it. The consent names the
   provider and address and lapses when you change the model. With **Local models only**, a cloud model is
   refused even with consent.
3. Each screenshot is approved by you (**Ask before each screenshot**), or you allowed screenshots for one hour.

What happens to a screenshot:

- Password fields are blacked out **on your computer** before the image leaves it.
- It goes to ROOKS and on to the vision model, and nowhere else. It is not stored, not logged, and not shown to
  the chat model, which only receives the position, the label the vision model read, and its confidence.
- The Computer page and the bar at the top of every page show "Screen shared" for 15 seconds after each one.

What a visual click checks:

- The target expires after 2 minutes.
- If the window moved or changed size since the screenshot, the click is refused.
- Below 0.75 confidence, or a position outside the window, ROOKS says it is not sure and asks you, never guesses.
- A label like Send, Delete or Buy read on screen still needs your approval.
- Blocked apps are never captured. Visual targeting never bypasses the app rules or the mode you set.

## File changes, versions and undo

ROOKS's file tools (in folders you allowed) keep the previous version before replacing a file, verify every write
by reading it back, skip a write when the content is already identical (so a retry after a restart does not write
twice), and never delete anything. Spreadsheets (.csv, and .xlsx with openpyxl) and documents (.txt, .md, and
.docx with python-docx) can be read and edited directly, which is more exact than clicking in an app; results go to
a new file unless you approve overwriting. The Computer page lists these changes with **Undo**, which restores the
earlier version and refuses if the file changed since. Undo covers ROOKS's file tools only: actions taken by
clicking in other apps cannot be undone by ROOKS.

Optional on the computer: `python3 -m pip install --user openpyxl python-docx` (both MIT). CSV works without them.

## Checking on your Mac

`cd ~/ai-workspace/companion && python3 tests/macos_check.py` runs the macOS adapter against real apps on your Mac
and writes `~/rooks-macos-check.json`. It checks permissions, displays and Retina scale, focus, TextEdit's
accessibility tree, typing, setting text exactly (non-ASCII), Edit > Select All, saving and reading the file back, a
save sheet ("Don't Save" refused without approval), Stop during typing, window capture and a refused click after the
window moved, Finder, and file moves with versions and undo. It works only in a new temporary folder with TextEdit
documents it opens itself, and closes what it opened; it does not ask before each step. It needs Accessibility and
Automation for System Events for Terminal; capture checks are skipped without Screen Recording. **It has not been
run on a Mac in this work**, so its result will be the first real evidence for the macOS adapter.

## What is tested, and how

| Test | Method | Result |
|---|---|---|
| Companion engine rules: ids expire, labels re-checked, approval enforced on the computer, password fields, blocked apps, read only, folders including link escapes, journal replay, Stop mid-typing, local STOP file | `companion/tests/test_engine.py`, in-memory desktop | 10 of 10 |
| File versions, verified writes, no duplicate writes, undo and its conflict check, CSV, .xlsx and .docx editing | `companion/tests/test_files.py`, real files in a temporary folder | 7 of 7 |
| macOS adapter: arguments never in script text, error and timeout messages, moved elements, focus check, menus, Retina and multi-display coordinates | `companion/tests/test_macos_adapter.py`, **osascript mocked** (not a Mac) | 11 of 11 |
| ROOKS rules: pairing, auth, private network only, policy, approvals, grants, web content, Stop, take control, recovery, log privacy | `workbench/backend/tests/test_computer.py`, fake companion over the real HTTP routes | 11 of 11 |
| Visual targeting: image test gate, consent and destination, local only, approvals, confidence, no fallback, nothing logged | `workbench/backend/tests/test_vision.py`, fake companion and fake vision model | 5 of 5 |
| Scenarios A to G, V, T and a mission (see below) | `companion/tests/e2e/e2e.py`: real ROOKS process, real companion process, real X server (Xvfb) with XTEST input, real Chromium, real files, real process kill | 37 of 37 |

The scenarios run in that last suite:

- **A: app.** Observe, click by element id, and verify the window title changed by exactly one.
- **B: files.** Organize fixtures into folders and verify the result on disk.
- **C: browser.** Fill a form; the password field is refused; Submit waits for approval and is then approved; the
  website log shows exactly one submission; the thank-you page is verified.
- **D: document.** Type and save, then verify the saved file.
- **E: Stop.** Typing stopped at 160 of about 3,150 characters, and nothing arrived after that.
- **F: denial.**
  - The app is blocked on the server before anything is sent.
  - A label lie is refused by the companion.
  - A denied Delete leaves everything unchanged.
- **G: recovery.** The ROOKS process was killed (SIGKILL) while typing. The late result was recorded after restart,
  a repeat was refused until the screen was observed, and the line exists exactly once.
- **V: visual targeting.** A button the fixture app does not publish is found in a real screenshot and clicked once;
  the password field arrived blacked out; a click after the window moved is refused; a target that is not there is
  reported as not found. The vision model here is a **stand-in** that decodes the image and finds red pixels, not
  a real model.
- **T: workflow template.** The spreadsheet cleanup template ran as a mission on a real CSV: one row per email in
  the new file, the original untouched.
- **Mission.** Planned, approved, run with file tools, and verified done.

**Not tested here:** the macOS adapter has never run on a Mac in this work (its tests mock osascript). Its scripts and `ctypes` calls follow
Apple's documented APIs, but treat it as untested until it runs on your Mac. Start in *Look only* mode, then try a
harmless app.

The X11 tests use a small fixture app that publishes its controls the way an accessibility API would. Real Linux
apps would need AT-SPI support, which is not built.

## Windows

`companion/rooks_companion/adapters/windows.py` documents the planned mapping onto UI Automation, method by
method. On Windows today, the companion reports desktop control as unavailable. ROOKS's browser and allowed
folders work there, because they are cross-platform.


## Legacy action-history privacy cleanup (manual and optional)

Versions before v9.1 may have stored screenshots or typed text in SQLite action records,
conversation tool-call history and approvals. The v9.1 code prevents new Computer Use
result persistence and encrypts new pending Computer Use approval arguments, but **it
does not automatically remove historical data**.

After stopping ROOKS and the companion, use the standalone cleanup script to inspect
an **existing** database file, with the actual path from your installation:

```sh
python3 workbench/backend/scripts/scrub_computer_history.py --db /path/to/your/rooks.sqlite3
```

The default operation is a dry run. To modify the database you must also provide
`--apply --confirm PURGE-COMPUTER-HISTORY --backup /private/new-backup.sqlite3`.
The backup contains the original private material and should be secured separately.
The cleanup refuses to run while Computer Use actions or approvals are active; do not
attempt it while the application is running. **Do not run it on an irreplaceable database
without a verified recovery procedure.**

The cleanup operates on known Computer Use history fields. It does not claim to purge
every potentially sensitive quotation from ordinary chat messages, mission reports,
external logs, snapshots, backups or remote copies. Historical data can persist in
database pages, WAL files and old exports; coordinate broader erasure separately.

## Update provenance

A `.sha256` file placed alongside an archive detects accidental corruption but does
not establish who published either file. Install updates only from an independently
trusted source; signed release verification is still future hardening work.
