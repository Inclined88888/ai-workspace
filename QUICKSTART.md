# AI Workspace: Quick start (v0.1 release candidate)

One command installs everything for your user account, with no admin rights, Docker or Node.js needed:

* **AI Workspace**, the app: chat, memory, notes, tasks, calendar, mail drafts, automations, skills, research and the
  Browser agent page.
* **`rooks`**, the terminal coding agent (Rooks Code).
* **`rooks-browser`**, the AI browser agent (Rooks Browser), plus a browser for it. It uses your Chrome or Edge if you
  have one, and otherwise downloads Chromium.
* **`ai-workspace`**, the one command that opens the app. It also adds a Start-menu / Applications / app-menu shortcut.

All three use **the same models**. When the app is open, `rooks` and `rooks-browser` route through its smart router, so
they use your keys and your free tiers, fall back when a provider hits a limit, and log every call under the app's
**Usage** page. You don't need to set up anything twice.

## 1. Install

### macOS (and Linux)

Open **Terminal** and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install.sh | bash
```

### Windows 10 / 11

Open **PowerShell** (Start → type *PowerShell* → Enter) and paste:

```powershell
irm https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install.ps1 | iex
```

**Before this release candidate is merged to `main`**, install from its branch:

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/release/v0.1-rc/install.sh | AI_WORKSPACE_REF=release/v0.1-rc bash
```
```powershell
# Windows
$env:AI_WORKSPACE_REF='release/v0.1-rc'; irm https://raw.githubusercontent.com/Inclined88888/ai-workspace/release/v0.1-rc/install.ps1 | iex
```

**From the release zip** (no download step), unzip `ai-workspace-0.1.0-rc1.zip`, then either:

* macOS / Linux: run `bash install.sh` in that folder, or
* Windows: right-click `install.ps1` → **Run with PowerShell**.

**What the installer does** (about 1–3 minutes):

1. It installs [uv](https://docs.astral.sh/uv/), a small tool that brings its own Python 3.12, into `~/.local/bin`.
   Your system Python isn't touched.
2. It installs the app into `~/.ai-workspace`, and `rooks` and `rooks-browser` as uv tools.
3. It finds Chrome or Edge, or downloads Chromium for the browser agent.
4. It adds the `ai-workspace` command and a shortcut.
5. When a signed or unsigned **native app** (.dmg / .exe) is published on GitHub Releases for your computer, it installs
   that too. Otherwise the app opens in its own Chrome/Edge app window. That's the same app with the same data.
6. It opens the app.

Installer options:

* `--no-start` (Windows: `$env:AI_WORKSPACE_NO_START=1`): install without opening the app.
* `--native no` (Windows: `$env:AI_WORKSPACE_NATIVE='no'`): skip the native app download.

Run the same command again to upgrade. Your chats, keys and settings are kept.

## 2. First run

1. Run `ai-workspace` (or click the **AI Workspace** shortcut). The app opens in its own window.
2. **Choose how to think**:
   * **Free cloud** (recommended): paste a free Gemini or Groq key. It takes two minutes and needs no card. The screen
     links to the key pages.
   * **Sign in with your subscription**: use your ChatGPT plan.
   * **Paste API keys**: OpenAI, Anthropic, xAI, Google, Groq, OpenRouter, Mistral, DeepSeek or any OpenAI-compatible URL.
   * **Local Ollama**: private and offline. If [Ollama](https://ollama.com/download) is running, the app finds it and
     its models.
3. You land in **Chat**. Ask anything. The assistant can use your notes, tasks, calendar and mail drafts, and every
   action it takes is logged.

Keys stay on your computer, in the system keychain (macOS Keychain, Windows Credential Manager or Secret Service). If
no keychain is available, they go in an encrypted file in the app's data folder.

## 3. The agents

```bash
cd ~/my-project
rooks "the tests in test_calc.py fail; fix them and run the tests"   # one task, then exit
rooks                                                                 # interactive session
rooks undo                                                            # put back the last task's file changes
rooks doctor                                                          # which model/provider it will use

rooks-browser "find the price of the USB-C hub on example-shop.com"   # watch it work in a real browser
rooks-browser doctor                                                 # browsers and models it can use
```

* **Rooks Code** asks before every edit and command. It skips the asking in a trusted folder, with `--yes`, or when you
  answer `a`. It shows a diff for each edit, and checks after each change by re-reading the file or reading the
  command's exit code.
* **Rooks Browser** shows the browser while it works when there's a display. Before it submits a form, buys
  something or sends a message, it asks you first. `--approve submit,purchase,message` pre-approves those for one
  run, but never logins. When it needs you to log in, it hands the step over to you.
* **When the app is open**, both agents use it automatically. You can see this in `rooks doctor`, which shows
  `provider: workbench`. When the app is closed, they use local Ollama, or a provider you choose (`rooks init` writes
  an example config; `rooks-browser run --help` lists its provider options).
* The app's **Browser agent** page drives the same Rooks Browser. The app starts it for you.

## 4. Commands

| Command | What it does |
| --- | --- |
| `ai-workspace` | Opens the app (and starts it if it's not running). Closing the window stops it. |
| `ai-workspace --status` | Shows whether it's running, the data folder, and where `rooks` / `rooks-browser` are. |
| `ai-workspace --stop` | Stops it. |
| `ai-workspace --no-open` | Starts the app without a window and prints a one-time sign-in link. |
| `ai-workspace --web` | Uses the app window even when the native app is installed. |

## 5. Unsigned-app warnings (native app)

The native desktop builds aren't code-signed yet. The first time you open them:

* **macOS (Gatekeeper)**: "AI Workspace can't be opened because Apple cannot check it…". **Right-click** (or
  Control-click) the app in Applications → **Open** → **Open**. You only need to do this once. On macOS 15 and later, if
  there's no Open button: System Settings → Privacy & Security → scroll down → **Open Anyway**.
* **Windows (SmartScreen)**: "Windows protected your PC". Click **More info** → **Run anyway**.

The `ai-workspace` command and the app window it opens (the default when no native app is published) don't trigger
either warning, because they run from your own user folder.

## 6. Where things live

| | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Program | `~/.ai-workspace` | `%USERPROFILE%\.ai-workspace` | `~/.ai-workspace` |
| Commands | `~/.local/bin` | `%USERPROFILE%\.local\bin` | `~/.local/bin` |
| Your data (chats, notes, settings, logs) | `~/Library/Application Support/com.inclined88888.aiworkspace` | `%APPDATA%\com.inclined88888.aiworkspace` | `~/.local/share/com.inclined88888.aiworkspace` |
| Agent ↔ app link (private, 0600) | `~/.config/ai-workspace/app.json` | `%APPDATA%\ai-workspace\app.json` | `~/.config/ai-workspace/app.json` |

The native app and the `ai-workspace` window share the same data folder.

**Uninstall**:

1. Run `ai-workspace --stop`.
2. Run `uv tool uninstall rooks-code rooks-browser`.
3. Delete `~/.ai-workspace` and `~/.local/bin/ai-workspace` (Windows: `ai-workspace.cmd`), and the shortcut.
4. Delete the data folder above only if you also want to remove your chats and settings.

## 7. Known limits in this release candidate

* **Native .dmg / .exe / .msi installers aren't published yet.** The `Desktop app` GitHub Actions workflow builds them,
  but they're unsigned, and macOS and Windows builds haven't been opened on a real Mac or PC by us yet. Until they're
  published, `ai-workspace` opens the app in a Chrome/Edge app window, which is the same app.
* **Tested end to end on Linux only**: clean user, one command, onboarding, chat, `rooks`, `rooks-browser`. On macOS and
  Windows, the installer is checked in CI (install, start, answer, stop) but hasn't been run by hand.
* **Small local models are weak agents.** With Ollama 7B models on a CPU, chat answers take 30–120 s. They sometimes
  call tools they don't need, and `rooks` sometimes describes a change instead of making it, so retry the task or use a
  coder model (`rooks -m 1/qwen2.5-coder:7b`). A free Gemini/Groq key is much faster and more reliable.
* **Web search** needs a Brave Search or Tavily key (Settings → Search). Until then the assistant doesn't offer it.
* **Claude Pro/Max subscriptions can't be connected** (Anthropic's terms). Use a Claude API key.
* The mobile app (Expo) pairs with a running desktop app. See [docs/mobile-pairing.md](docs/mobile-pairing.md). It isn't
  in the app stores.
