# Rooks Desk: computer use for ai-workspace

Give it a task in plain English and it uses your computer the way you would. It looks at the screen, clicks, types,
presses shortcuts, opens apps, runs terminal commands and works with files. Before anything risky it asks you.

```
rooks-desk "open Mousepad, write a haiku and save it as ~/Documents/haiku.txt"
rooks-desk run --dry-run "rename every .jpeg in ~/Pictures/trip to .jpg"
```

The app's **Computer** page, the assistant (its `use_computer` tool), Rooks Code (the MCP server `desk`) and any MCP
client all drive the same agent.

| | macOS | Windows | Linux (X11) |
|---|---|---|---|
| Screenshots (all monitors, downscaled for the model) | mss | mss | mss |
| Mouse, keyboard, hotkeys | pynput (Quartz) | pynput (SendInput) | XTest |
| Apps and windows | `open -a`, AppleScript | UI Automation | EWMH (python-xlib), `gtk-launch` |
| Accessibility tree (numbered elements) | AX (pyobjc) | UI Automation | AT-SPI (`python3-pyatspi`) |
| Shell (streams output), files, clipboard | yes | yes | yes |
| Tested on | CI (macos-14, no GUI) | not yet | Xvfb, real apps |

Wayland can't be driven from outside, so on Linux run an X11 session. Mac and Windows backends are written against
the platform APIs, but nobody has run them end to end on real hardware yet. See "Gaps" below.

## How it works

For each step: **plan → act → verify.**

1. **Look.** It takes a screenshot (downscaled to 1280 px wide, with the mouse marked) and reads the accessibility tree. That
   gives a numbered list of what is on screen, for example `[7] button 'Save'`.
2. **Decide.** It sends the model the goal, the history, the last result, the element list and the screenshot, and
   asks for one action as JSON.
   - A model that can see gets the image.
   - A text-only model gets just the element list, which is the accessibility-text fallback.
   - Through the app, the smart router sends screenshots to a vision model when one is set up.
3. **Act.** The step goes through the policy (below), runs, and is recorded with a screenshot.
4. **Verify.**
   - The next screenshot shows whether it worked.
   - When the model says `done` but a file the task asked for (`save it as ~/x.txt`) doesn't exist, the claim is
     rejected once with `VERIFY FAILED: … does not exist`. A second false claim is reported as a failure, not a
     success.
   - It notices when it repeats the same action and tells the model to try something else.

Free models that can see screenshots (checked October 2026):

- **Gemini:** 2.5 Flash and newer, on the free tier with an AI Studio key.
- **Groq:** `qwen/qwen3.8-27b`, `meta-llama/llama-4-scout`, `meta-llama/llama-4-maverick`, and
  `meta-models/muse-spark-open` (preview).
- **OpenRouter:** free variants such as `google/gemma-3-27b-it:free`, `meta-llama/llama-4-maverick:free` and
  `qwen/qwen2.5-vl-72b-instruct:free`.
- **Ollama, locally:** `qwen2.5vl`, `llava`, `llama3.2-vision`, `minicpm-v`, `moondream`.

These can't see images: Groq `gpt-oss`, `*-coder` models, DeepSeek chat and reasoner, Kimi, and Llama 3.0 to 3.3.
They still work through the element list, but they're much weaker at GUI work. In our tests a 7B text model got
partway through the save dialog and then claimed success it hadn't earned. The verify step catches that.

## Safety

- **It asks you** (in the app, the terminal or the MCP client) before:
  - a shell command (except read-only ones like `ls` and `git status`), and before typing a command into a terminal
    app. Read-only commands that print file contents (`cat`, `grep`, `head`, …) still ask when they name a file
    outside your folders, and `env`/`printenv` always ask;
  - writing, moving or deleting outside the folders you allowed, and before any delete;
  - sending email or messages, posting or sharing;
  - buying or paying;
  - signing in, or anything that touches passwords or card numbers. It never types into password fields.
  - Dangerous commands (`rm -rf`, `sudo`, `mkfs`, `curl | sh`, …) and logins can't be pre-approved.
- **Its own keys stay out of reach.** Commands it runs don't see the app's bridge key or Rooks Desk's token.
- **Allow-lists.** Folders (`rooks-desk folders add ~/Projects`) and apps (`rooks-desk apps allow Mousepad`).
  Password managers are denied by default.
- **You can always see it.** A red frame and a bar reading "Rooks Desk is using your computer · Ctrl+Alt+Esc to
  stop" stay on screen, with a Stop button.
- **Kill switch.** Any of these stops it within a second and refuses every action for 30 s:
  - Ctrl+Alt+Esc;
  - moving the mouse into the top-left corner;
  - `rooks-desk stop`;
  - "Stop everything" in the app.
- **Receipts.** Every step is a line in `~/.rooks-desk/runs/<session>.jsonl` with its screenshot. The app shows them
  on the Computer page.
- **Dry run.** `--dry-run` (or the checkbox) plans and records every step without touching anything.

### Permissions

- **macOS.** Run `rooks-desk setup`. It opens **System Settings → Privacy & Security** at **Screen Recording** and
  **Accessibility**, so you can switch on the app that runs Rooks Desk (Terminal, iTerm or AI Workspace), then it
  checks both. Restart that app afterwards.
- **Windows.** It runs as you, without admin rights, so it can't click inside elevated windows or UAC prompts
  (Windows blocks that on purpose). If a task needs admin rights, you approve the UAC prompt yourself. Rooks Desk
  hands that step to you and waits.
- **Linux.** Use X11. Install `python3-pyatspi` (or `python3-gi` plus `gir1.2-atspi-2.0`) for the accessibility
  tree.

## MCP

`rooks-desk mcp` is an MCP server on stdio with `use_computer` (a whole task) plus low-level tools: `screenshot`,
`click`, `type_text`, `hotkey`, `scroll`, `drag`, `open_app`, `list_windows`, `focus_window`, `elements`,
`run_shell`, `read_file`, `write_file`, `list_dir`, `search_files`, `move_file`, `clipboard_get` and
`clipboard_set`.

- **Rooks Code** adds it automatically when `rooks-desk` is on PATH. Turn that off with `[mcp] desk = false` or
  `ROOKS_DESK_MCP=0`.
- **Claude Code:** `claude mcp add rooks-desk -- rooks-desk mcp`.

Sensitive steps become an MCP elicitation question to you. Clients that can't ask you get a refusal instead, because
the calling agent can't approve for you.

Rooks Desk can also use other MCP servers, for example an AutoCAD one:
`rooks-desk mcp-servers add autocad -- uvx autocad-mcp-pro`. Their tools need approval unless you `trust` the
server.

## AutoCAD

See [docs/AUTOCAD.md](docs/AUTOCAD.md) for the honest recipe ("draw this in AutoCAD, render it in 3D, email me").

## Tests

```
pip install -e '.[dev]'
pytest -q                                   # unit tests with a fake desktop
ROOKS_DESK_E2E=1 DISPLAY=:99 pytest -q tests/test_e2e_linux.py   # real apps under Xvfb (Mousepad, xfce4-terminal)
ROOKS_DESK_E2E_MODEL=ollama:qwen2.5vl ...   # drive the E2E with a real model instead of the scripted one
```

## Gaps

- The macOS and Windows backends haven't been run end to end on real machines. CI only imports them and runs the
  unit tests on macos-14.
- Wayland isn't supported.
- No free model is reliable at long GUI tasks yet. Small local models are text-only and over-claim, which the verify
  step catches but can't fix.
