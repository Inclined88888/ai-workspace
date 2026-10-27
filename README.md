<p align="center"><img src="docs/rook-mark.svg" width="72" height="72" alt="ROOKS logo"></p>

<h1 align="center">ROOKS</h1>

<p align="center"><strong>Your move. ROOKS delivers.</strong></p>

ROOKS is a lightweight, self hosted AI workspace: a dependable right hand that listens, understands what you ask, coordinates tools and agents, and gets the work finished. It runs in **one light container**. Chat with an assistant that remembers you and learns skills, plus notes, documents, tasks, calendar, mail, research with sources, scheduled reports, a company of AI agents with budgets, and model comparison. Runs on Anthropic, OpenAI, OpenRouter or any OpenAI compatible provider, or local models.

**The core is one app** (the `workbench` service): FastAPI + SQLite + React, one image (about 350 MB), about 100 MB of RAM at idle (measured on the backend process; real use depends on traffic). No GPU, no database server, no extra containers. A small VPS with 1 GB of RAM or a Mac laptop runs it.

Everything heavier is an **optional add-on, off by default**:

| Add-on | Turn on with | Adds | Cost |
| --- | --- | --- | --- |
| `search` | SearXNG | Free private web search (otherwise use a Brave or Tavily key, or none) | small |
| `browser` | browser-use + Chromium | Browser agent page and a `browse_web` tool | ≈ 2.5 GB image |
| `local-models` | Ollama in Docker | Local models (on a Mac, native Ollama is faster and is detected by setup) | ≈ 8.5 GB image + models |
| `openwebui` | Open WebUI | A second chat app with RAG knowledge bases and multi user accounts | ≈ 6.5 GB image |
| `paperclip` | Paperclip | Upstream company of agents running coding CLIs (Claude Code, Codex, OpenCode) | ≈ 13 GB image |
| `hermes` | Hermes Agent | Upstream personal agent with its own gateway (WhatsApp, Signal, Slack) | ≈ 4.3 GB image |

You don't need any of them for the assistant, memory, skills, schedules, Telegram or the company of agents. Those are built into the core.

## Quick start

Requirements: Git and Docker with Compose v2 (Docker Desktop or OrbStack on a Mac), 1 GB RAM free, about 2 GB of disk for the build. At least one model provider key, or Ollama.

One command (Linux, macOS, or a Windows WSL2 Linux terminal; Docker must already be running):

```bash
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/security/rooks-v9-1/install.sh | AI_WORKSPACE_REF=security/rooks-v9-1 bash
```

With add-ons, or with no questions:

```bash
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/security/rooks-v9-1/install.sh | AI_WORKSPACE_REF=security/rooks-v9-1 bash -s -- --with search,browser
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/security/rooks-v9-1/install.sh | AI_WORKSPACE_REF=security/rooks-v9-1 bash -s -- --yes
```

The installer checks Git, Docker and Compose v2, downloads the code to `~/ai-workspace` (`AI_WORKSPACE_DIR` changes that), and runs `setup.sh`. It never installs system packages or runs sudo. Existing installs must have the expected origin and no local edits or untracked files; updates must fast-forward. Archive installs require a fresh destination. Existing `.env` values and data are kept.

These commands explicitly select the security branch; `main` has not been updated. A branch URL can change. For a reviewed version, replace **both** occurrences of `security/rooks-v9-1` with the same full commit SHA. Prefer to inspect it first? Download `install.sh` from that commit, read it, then run `AI_WORKSPACE_REF=<that-SHA> bash install.sh`. Commit pinning selects exact repository code; it is not signed release authentication or a fully pinned dependency build.

Supported installer targets are 64-bit Intel/AMD and ARM on macOS/Linux and Windows through WSL2 with Docker integration. Native Windows shells are refused. Full installations on every OS/architecture are not verified.

From a clone instead:

```bash
git clone --branch security/rooks-v9-1 https://github.com/Inclined88888/ai-workspace && cd ai-workspace
./setup.sh                        # core only
./setup.sh --with search,browser  # core plus add-ons (comma separated)
./setup.sh --with none            # turn all add-ons off again
```

`setup.sh` creates `.env` (mode 600) with generated secrets, asks for a name, your timezone (detected), LAN access and your provider keys, then builds and starts. If Ollama is already running on the host (`localhost:11434`) setup offers to connect it. It prints the URL (`http://localhost:3100`) and a one time **setup code**: open the URL, enter the code, choose a password.

Re-running is safe and is how you upgrade or change add-ons: it adds new settings, keeps secrets and data, and stops add-ons you turned off. `--yes` runs unattended with defaults.

### On a Mac

```bash
# 1. Install and start Docker Desktop or OrbStack.
# 2. Optional, free local models on the Mac GPU:
brew install ollama && brew services start ollama
ollama pull qwen3:4b
# 3. Install:
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/security/rooks-v9-1/install.sh | AI_WORKSPACE_REF=security/rooks-v9-1 bash
```

Native Ollama uses the Mac GPU; the `local-models` add-on would run on CPU inside Docker's VM, so skip it on a Mac.

## The assistant

**Assistant** is the main page: one chat that can use every part of the workspace.

* **Tools.** Notes (search, read, create, append), tasks, calendar (agenda, create event), mail (search, save draft only, never send), web search and page reading, the browser agent when that add-on is on, memory, skills and schedules. Each tool step shows live in the chat.
* **Permissions.** Settings → Security → *What the assistant may do*. Each area (notes, tasks, calendar, email, files, web, browser, memory, skills, schedules, team) is **Allowed**, **Ask me first** or **Off**. Ask me first lets it look things up but makes every change wait for your approval. Off removes those tools. The server enforces this for chat, Telegram, schedules, company agents, the Open WebUI tool server and MCP clients. Outside clients cannot ask for approval, so Ask me first means read only for them.
* **Limits.** The same tool with the same arguments three times in one request is refused, and a request that keeps repeating itself is stopped. Each request has a time budget (`WORKBENCH_TURN_SECONDS`, default 300). When it runs out you get what was found so far.
* **Activity log.** Settings → Security lists sign-ins (including failed ones), password, key, provider and permission changes, approvals, and every action that changed something. Key values and passwords are never written to it.
* **Deep Research.** Your question is split into sub-questions and searched on every configured search source. ROOKS then:
  - reads pages that allow it (robots.txt), and folds duplicates and tracking-link variants;
  - rates each source for relevance, freshness (from its date) and a stated credibility estimate based on the kind of site;
  - extracts claims only with a quote that is actually found on the page, and drops any whose quote is not there.

  The report has separate sections for the answer, key findings, where sources disagree, inferences (labelled) and open questions. Citations to sources that do not exist are removed by code. Every source can be opened to see its date, credibility reasoning, quoted claims and how it was found. A run can be cancelled and run again.
* **Memory.** After a conversation it quietly extracts durable facts about you (profile, preferences, facts), at most 5 per conversation, skipping duplicates. When a fact changes (you moved, changed jobs) it updates the old memory instead of adding a contradiction, and keeps the previous wording. It never stores API keys, tokens, passwords or card numbers. They are refused wherever a memory is written. Relevant ones are injected into every new conversation. You can also say "remember that…" or "forget…". Review and edit everything under **Memory & skills**. Turn learning off in Assistant settings.
* **Skills.** When a task took 3 or more tool calls, it can save the procedure as a named skill and improve it on later use. Skills are listed to the model and loaded on demand, so they cost almost nothing until used.
* **History.** Every conversation is full text searchable, by you and by the assistant (`search_history`).
* **Models.** Switch per conversation from the model menu. Any provider in Settings works.
* **Terminal.** `cli/ws` is a zero dependency Python CLI: `cli/ws login`, then `cli/ws` for a REPL or `cli/ws "what's on today?"` for one shot. Also `cli/ws memory`, `skills`, `inbox`, `schedules`.
* **Telegram.** Assistant settings → Telegram: paste a bot token from @BotFather and your numeric user id. Only allowlisted ids get answers; others are told their id so you can add them. `/new` starts a fresh conversation. The token is stored encrypted.

**Missions.** For a bigger job ("compare three CRM tools and save a summary note"), use **Missions** in the sidebar or type `/mission …` in chat.
- **Plan first.** ROOKS writes a plan in which every step has a "done when" check. You can edit the plan, and nothing runs until you approve it.
- **Every step is checked.** Each step is an ordinary assistant turn, so permissions and approvals apply. Afterwards a separate check compares what happened with the step's criterion. A step that needed a tool but had none succeed is never marked done: it shows as *Not verified*. A step that fails its check is retried once, and repeating without progress counts as stalled.
- **Approvals on top of your permissions.** Missions always ask before acting outside ROOKS (browsing sites, recurring schedules), whatever your permission settings.
- **Survives a restart.** Progress is saved after every step. After a restart the mission resumes at the step that was running. Actions it already took are recorded and replayed, never repeated, so a recovered step cannot create a second note or task.
- **Limits.** Each mission has limits on tool calls, minutes and optionally spend. Reaching one pauses the mission; raise the limit and resume.
- **Controls and result.** Pause, Resume, Cancel and "Run again from the first unfinished step" are all on the mission page, with live progress, an activity log and a final report that lists what was done, what failed and what could not be verified.

**Computer.** With the optional ROOKS Companion running on your computer, ROOKS can use desktop apps (macOS), its own separate browser, and folders you allow. You can ask in chat ("open my invoices spreadsheet and sort it by date") or as a Mission.
- **Off by default.** Turn it on under **Computer** and choose *Look only*, *Ask before changes* or *Allowed*. You can also set rules per app.
- **Always asks.** Sending, buying, deleting, installing, publishing and overwriting always wait for your approval.
- **Never touched:** shells, password managers and system security settings.
- **Visible and stoppable.** A bar shows on every page while ROOKS acts, with Stop and Take control.
- **No open port.** The companion only connects out to ROOKS.
- **Platform status:** macOS desktop control has not yet been tested on a real Mac; Windows supports the browser and folders only.

Setup and the full safety model are in `docs/COMPUTER_USE.md` and `companion/README.md`.

**Automations.** Cron schedules in your timezone ("Daily report, 8:00" is one click). Results go to the Inbox and optionally Telegram. You can also ask in chat: "every Friday at 5pm summarise my week". Scheduled runs cannot create more schedules. The next run is booked before each run, so a failing job cannot loop.

**Company.** Set a mission, hire agents (CEO, CTO, researcher…) with a model, instructions, who they report to, a **monthly budget in dollars** and a **monthly token limit**. Give a task to any agent; managers can `delegate_task` to their reports (up to 3 levels deep). The org chart shows spend bars; the board shows To do, Queued, Running, Done and Needs you. An agent over its limit is **paused automatically** until you raise the limit or the month rolls over. Limits are checked before every model call, so spend can overshoot by at most one call. OpenRouter reports real cost; for other providers set your price per 1M tokens in Settings → Usage, otherwise dollar budgets cannot be enforced (token limits always are).

## Workspace features

**Projects.** One place per client, job or goal: description, goals, instructions for ROOKS, decisions and open questions, and the conversations, missions, tasks and notes that belong to it. Every chat or mission started in a project begins with its brief, so ROOKS carries on without being told again. Facts learned in a project are remembered only there; who you are and how you like things done stay user wide. Per project you can narrow permissions (never widen them), require local models only (cloud models are refused, including as backups), choose a model and set a monthly budget. Archive hides a project; the bin keeps everything; deleting for good needs the project's name typed and keeps its conversations and files, unlinked. See `docs/PROJECTS.md`.

**Terminal and workspace.** The assistant can run commands and create, read and edit files in its own workspace folder, and use the results to keep going (run the tests, read the failure, fix the file, run them again). It all happens in a separate runner container that has only the workspace: no ROOKS data, no API keys. The terminal asks before each command by default (read-only commands on your allow list excepted); risky commands, anything after reading web content, and runs without you watching always ask. Output, exit codes and failures show in the chat steps. See `docs/TERMINAL.md`.

**Workflows.** Missions can start from templates for spreadsheet cleanup, invoice checks, document edits, email drafts, follow-ups, a weekly report and inventory. A template only writes the plan; you review it before anything runs, drafts are never sent, and files are written as new files unless you approve overwriting.

**Files.** Upload PDFs, Word documents (.docx), Markdown, CSV, HTML and code files (up to 25 MB each) on the Files page, or attach them in chat with the paperclip or by dropping them in. Their text is extracted so ROOKS can answer questions about them and search inside them. Images and spreadsheets are stored but not read yet, and scanned PDFs have no text without OCR. Uploaded files count as outside content, so persistent actions after reading one wait for your approval.

**Appearance.** Settings, Appearance: 14 themes (light, dark and high contrast) or your own, nine bundled fonts (including Atkinson Hyperlegible for low vision and OpenDyslexic for dyslexia), text size, density, corner style and a subtle background pattern. Every theme, including custom ones, is adjusted so text keeps WCAG AA contrast. Fonts ship with ROOKS, so nothing is requested from font services, and your choice is saved to the workspace so it follows you between devices.

**Second chat app (`openwebui` add-on).** Streaming chat with any connected model; file uploads and knowledge bases with retrieval; memory (Settings → Personalization); web search through SearXNG when the `search` add-on is on (toggle per chat); multi model chat (pick several models in the model selector to get answers side by side); custom models with system prompts and tools.

**ROOKS tools in Open WebUI.** The `ROOKS` tool server is preconfigured. Enable it per chat from the tools button in the message box, or attach it to a model in Admin → Models. Tools: `search_notes`, `read_note`, `create_note`, `append_to_note`, `list_tasks`, `create_task`, `update_task`, `get_agenda`, `list_events`, `create_event`, `list_calendars`, `search_email`, `read_email`, `save_email_draft`, `web_search`. There is deliberately **no send email tool**: agents can only save drafts, which you review and send under Mail → Drafts. Tool calling quality depends on the model; small local models are hit and miss, larger ones (Qwen3 8B+, or cloud models) are reliable.

**Research.** Ask a question; the model plans several searches, ROOKS queries your search provider (SearXNG add-on, or a Brave or Tavily key in Assistant settings), reads the pages (internal and private network addresses are refused), and the model writes a Markdown report with numbered inline citations and a source list. Save any report to Documents.

**Compare.** Up to four columns, each with its own model and conversation. One prompt goes to all of them; each column shows total time, time to first token, tokens and tokens per second.

**Notes and documents.** Markdown with live preview, tags, pinning, full text search (SQLite FTS5) and autosave. AI assist works on the selection or the whole text: improve, fix grammar, shorten, expand, summarize, continue, outline, or a free form instruction. You see the result before it replaces anything.

**Tasks.** Due dates, priorities, projects, grouping by Overdue / Today / Upcoming, and "break down a goal" which turns one sentence into a task list.

**Calendar.** Month view plus day agenda. Sources:
- **ICS subscription** (read only): Google Calendar "Secret address in iCal format", Outlook published ICS, any `.ics`/`webcal://` link. Refreshes every 15 minutes.
- **CalDAV** (read and create): Nextcloud, Fastmail, iCloud (`https://caldav.icloud.com` with an app specific password), Radicale, Baïkal. Use *Find calendars* to pick one.
- **Local** calendar stored in ROOKS.

**Mail.** IMAP/SMTP with app passwords: Fastmail, iCloud, Gmail (needs 2 step verification and an app password), and most self hosted servers. Read, search, reply, compose, and "Draft with AI" with your intent. AI drafts use `[placeholders]` instead of inventing facts; every send asks for confirmation. Outlook.com and Microsoft 365 are **not** supported because Microsoft requires OAuth for IMAP.

## Add-ons in detail

The sections below only matter if you turned the add-on on.

### Company of coding agents: Paperclip (`paperclip` add-on)

You define the company and its goal, then hire agents into roles. Paperclip gives you the org chart, a task board, a dashboard of runs, spend and approvals, and a monthly budget per agent and per company. Per [Paperclip's docs](https://github.com/paperclipai/paperclip/blob/master/docs/guides/board-operator/costs-and-budgets.md), an agent gets a warning at 80% of its budget and is **auto paused at 100%** until you raise the budget or the month rolls over. The UI works on phones (bottom tab bar).

Hiring an agent (Agents → New agent) means choosing an adapter, which is the program that does the work:

| Adapter | Runs | Credentials |
| --- | --- | --- |
| **OpenCode** | Any OpenCode model: `ollama/<model>` (your local models, free), `opencode/<model>` (OpenCode Zen; free tier models like `opencode/big-pickle`), plus any provider OpenCode supports | none for Ollama; `OPENCODE_API_KEY` for Zen |
| Claude Code | Anthropic models | `ANTHROPIC_API_KEY` or a Claude subscription login |
| Codex | OpenAI models | `OPENAI_API_KEY` or a ChatGPT login |
| Gemini CLI | Google models | `GEMINI_API_KEY` |
| OpenClaw | An [OpenClaw](https://github.com/openclaw/openclaw) gateway you run separately, joined with "Invite an external agent" | the gateway's token |

Paperclip's image ships the Claude Code, Codex, OpenCode and Gemini CLIs. OpenCode reads `paperclip/opencode.json` (mounted read only), which `scripts/sync-opencode-config.sh` writes: one entry per downloaded Ollama model plus the browser agent as an MCP server. **Run that script again after pulling new Ollama models**, then `docker compose restart paperclip`.

Things to know:

* **Model picker.** The new agent form defaults to Paperclip's "AI connection" catalog (OpenRouter models). To use local or Zen models, type the id with "Enter custom model…", e.g. `ollama/qwen3:8b` or `opencode/big-pickle`.
* **OpenCode free tier is rate limited.** In testing, an unauthenticated request reached Zen and was refused with "Free usage limit reached". Create a free account at opencode.ai and set `OPENCODE_API_KEY` for a dependable allowance. Some free models may use your prompts for training; check each model's terms on [opencode.ai/docs/zen](https://opencode.ai/docs/zen).
* **Free and local models cost $0**, so Paperclip budgets only bite on paid providers. They still burn CPU or GPU time; small local models are weak at multi step agent work.
* **Telemetry.** Paperclip enables anonymous telemetry by default (`telemetry.enabled` in `/paperclip/instances/default/config.json` inside the `paperclip` volume). Set it to `false` there and restart if you want it off.
* **Data** lives in the `ai-workspace_paperclip` volume (embedded Postgres with hourly backups, agent workspaces, uploads, secrets key).

### Hermes Agent (`hermes` add-on)

Hermes is the agent that gets better the longer you use it. What it adds over the other agents here:

* **Learning loop.** After a task it can write a reusable skill (a saved procedure) and improve it on later use. Skills live in its volume and show up in the dashboard.
* **Memory that persists.** It keeps notes about you and your work across sessions, and full text search over its past conversations.
* **Scheduled jobs.** "Every weekday at 8, check my agenda and overdue tasks and message me a summary" runs unattended with its built in scheduler.
* **Reach it anywhere.** Telegram, Discord, Slack, WhatsApp, Signal and more from one gateway. `./hermes connect telegram` (or the dashboard's Messaging → Telegram → Create with QR) sets it up and restricts the bot to your own account.
* **Subagents** for parallel work, context compression for long sessions, and loop guards that stop runaway tool calls when nobody is watching.

Day to day, use the `./hermes` command from the repository root (same as in [`assistant/`](assistant/README.md)):

```bash
./hermes                              # terminal UI
./hermes key openrouter               # store a key (hidden prompt)
./hermes use openrouter <model>       # switch model; also anthropic, openai, or ollama <model>
./hermes connect telegram             # or discord; only your account is allowed
./hermes config set timezone Europe/London   # schedules follow this
./hermes daily-report 08:00 telegram  # scheduled daily briefing
./hermes cron list                    # anything else passes through to the Hermes CLI
```

How it is wired here:

* **Chat:** pick the model `hermes-agent` (new installs; see the note below for existing ones).
* **Tools:** ROOKS (13 tools: notes, tasks, calendar, mail search and drafts) and the browser agent are attached as MCP servers. Hermes cannot send mail, only draft it.
* **Dashboard** at port 3300 (user `admin`, password printed by `setup.sh`, stored as `HERMES_DASHBOARD_PASSWORD` in `.env`): memory, skills, scheduled jobs, sessions, model and messaging setup.
* **Model:** `scripts/hermes-config.sh` picks Anthropic if `ANTHROPIC_API_KEY` is set, else OpenRouter, else your local `DEFAULT_MODEL`. It runs once; afterwards switch with `./hermes use ...` or the dashboard. `--force` reapplies the defaults.

Requirements and limits, verified in testing:

* **Hermes refuses models with less than a 64K context window.** `qwen3:4b` (the default, 256K) qualifies; `qwen3:8b` and `qwen3:0.6b` (40K) do not. For local models the window is pinned to 64K (`model.ollama_num_ctx`), because by default Hermes asks Ollama for the model's full window, and for `qwen3:4b` that cache alone would need tens of GB of RAM. Even at 64K, plan on roughly 8 to 12 GB of RAM for Hermes on a local 4B model.
* **Small local models can chat but not drive tools.** In testing, a 0.6B model answered a plain question (70 s on CPU) but called tools with malformed arguments until Hermes' loop guard stopped it. Use a cloud model or at least `qwen3:4b` on a GPU for agent work.
* **Existing installs:** Open WebUI stores connections in its database after first start, so on an install that predates Hermes, add it in Chat → Admin Settings → Connections: URL `http://hermes:8642/v1`, key `HERMES_API_KEY` from `.env`.
* **Hiring Hermes in Paperclip** (adapter "Hermes Gateway", API URL `http://hermes:8642`, key `HERMES_API_KEY`, Paperclip API URL `http://paperclip:3100`) is **not preconfigured**. Paperclip refuses plain HTTP to any host other than localhost. Your two options: put TLS in front of Hermes, or tick Paperclip's `dangerouslyAllowInsecureRemoteHttp` for that agent, accepting that the traffic is unencrypted on the Docker network between the two containers (it never leaves the machine). That is your call; this setup does not set it for you.
* Hermes' own browser tools are disabled in this image (no browser installed); it uses the browser agent over MCP instead.

### Browser agent (`browser` add-on)

Give a goal in plain English, for example "Go to weworkremotely.com and find three remote software engineer roles" or "On PCPartPicker find a compatible CPU, motherboard and RAM for an AM5 build". The agent opens a headless Chromium, decides each step with your chosen model, and reports back with the pages it visited.

* **ROOKS → Browser agent:** pick the provider and model, a step limit, and optionally lock it to specific sites. Watch the steps and a live screenshot.
* **Chat:** the `Browser` tool (`browse_web`, `read_page`) is preconfigured; enable it per chat like the ROOKS tool.
* **Paperclip employees** (OpenCode) and **Hermes** have it as the `browser` MCP server automatically.
* **Standalone:** `browser-agent/` is a normal Python package with a CLI (`browser-agent run`, `open`, `click`, `type`, `screenshot`, `text`, `links`) and an `examples/` folder. See [browser-agent/README.md](browser-agent/README.md).

Model choice: `BROWSER_LLM_PROVIDER=auto` uses Anthropic if `ANTHROPIC_API_KEY` is set, else Gemini if `GEMINI_API_KEY` is set, else local Ollama. Claude and Gemini are far more reliable at browsing than small local models; in testing a 0.6B local model invented an answer and claimed success, which is why the UI labels results as the agent's own claim.

**Cost control.** Browser runs started from Chat or by Paperclip employees are paid with the browser service's model key, **outside Paperclip's budgets**. Three limits apply to every caller: `max_steps` per run, `BROWSER_MAX_STEPS_LIMIT` (default 50) as the ceiling for any run, and `BROWSER_MONTHLY_STEP_LIMIT` (default 2000 steps per UTC month, 0 = unlimited). ROOKS shows this month's usage. For guaranteed $0, set `BROWSER_LLM_PROVIDER=ollama`.

**Safety.** The service refuses IP address URLs and the stack's own hostnames, and its one-off page reads refuse any host that resolves to a private address. That keeps it off your containers and LAN. A public hostname that resolves to a private address could still be reached during an agent run; don't rely on this as your only network boundary on a sensitive LAN. The agent can fill forms and click buttons on public sites, so be specific about what it must not do (buy, post, delete).

## Models and providers

**Local.** With native Ollama: `ollama pull qwen3:8b`. With the `local-models` add-on: ROOKS → Settings → Local models, or `docker compose exec ollama ollama pull qwen3:8b`. Rough memory needs: 4B ≈ 4 GB, 8B ≈ 8 GB, 14B ≈ 12 GB.

**Free.** Three ways to run at $0, all one click in Settings → Model providers (free models show "(free)" in every model menu):

* **Ollama** on your own machine. Fully private.
* **OpenRouter** with a free account key: every model ending in `:free` (about 17 at the time of writing, most with tool calling). Limits: 20 requests a minute and 50 a day, or 1000 a day once you have bought $10 of credits.
* **Google Gemini** free tier with a key from aistudio.google.com (Flash and Flash-Lite models). Google may use free tier prompts to improve its products.

**OpenCode.** OpenCode's free models (Big Pickle, the `-free` models) only work inside the OpenCode app; called from anything else they answer "Free models work in OpenCode". So ROOKS offers **OpenCode Go** (flat $10 a month) and **OpenCode Zen** (pay as you go) as providers, and hides the models those endpoints can't serve. For OpenCode's free models, use the `paperclip` add-on: its agents run inside OpenCode itself.

**Cloud.** ROOKS talks to any OpenAI compatible endpoint (Settings → Model providers, with presets for OpenAI, Anthropic, OpenRouter). Groq, Together, Mistral, LM Studio, vLLM and LiteLLM also work: enter the base URL and key. If a provider does not expose `/models`, type model ids in the Models field.

For Chat, keys entered during `setup.sh` are passed as `OPENAI_API_BASE_URLS`/`OPENAI_API_KEYS`. You can also add connections later in Chat → Admin Settings → Connections. Open WebUI stores settings in its database after first start, so later changes to these env vars are ignored unless you set `ENABLE_PERSISTENT_CONFIG=false`; changing them in the Admin UI is the simplest route.

Anthropic is reached through its [OpenAI SDK compatibility layer](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk). Anthropic describes that layer as intended for testing and comparison, not long term production use, and some features (prompt caching, strict tool schemas) are not available through it.

## Remote access and security

- By default every port binds to `127.0.0.1`. Reach it remotely with an SSH tunnel (`ssh -L 3000:localhost:3000 -L 3100:localhost:3100 you@server`) or a reverse proxy.
- For a public domain put Caddy, Traefik or nginx with HTTPS in front, set `WEBUI_PUBLIC_URL` and `PAPERCLIP_PUBLIC_URL` to the https URLs and `COOKIE_SECURE=true`, then `docker compose up -d`. Paperclip only answers on hostnames in `PAPERCLIP_PUBLIC_URL` and `PAPERCLIP_ALLOWED_HOSTNAMES`.
- Remote access from your phone: a VPN such as Tailscale is the simplest safe option. Add the Tailscale hostname to `PAPERCLIP_ALLOWED_HOSTNAMES`.
- ROOKS is a single owner app. Passwords are hashed with scrypt; mail, CalDAV and provider credentials are encrypted at rest with a key derived from `APP_SECRET`. **Back up `.env`**: if you lose `APP_SECRET`, stored credentials cannot be decrypted and must be re-entered.
- Ollama, SearXNG, the browser agent and Hermes' API are not published to the host. The Hermes dashboard is (port 3300, basic auth, bound to `BIND_ADDRESS`); it stores API keys, so keep it on localhost or behind a VPN.
- Hermes can run terminal commands inside its own container. Its tool approvals and loop guards apply, but treat it as having full access to its volume and whatever its tools reach. The browser agent requires `BROWSER_API_KEY` on every request, including MCP.
- The tool server requires `TOOLS_API_KEY` as a bearer token. Its OpenAPI description is readable without the key (it contains no data).
- Page reading (assistant, Research) only reaches globally routable addresses: no localhost, LAN, Docker networks, cloud metadata (169.254.169.254) or carrier-grade NAT ranges. Each redirect is checked again. The address actually connected to is checked after connecting, which defeats DNS rebinding (this check cannot apply when an outbound HTTP proxy is configured). Pages are capped at 2 MB.
- Deep Research reads only pages whose `robots.txt` allows it, identifies itself honestly as `ROOKS/1.0` rather than as a browser, and skips pages that refuse it. It does not get around logins, paywalls or CAPTCHAs.
- GitHub (optional, Settings → Connections) uses a token you create. Searching and reading are allowed; opening an issue or commenting always waits for your approval.
- Model costs: set a monthly spending limit in Settings, under Usage; model calls stop when it is reached. Connection checks send at most a few capped requests. A free default model never falls back to a paid one unless you allow it.
- What is built, partly built and missing, with test evidence for each of 100 requirements: `docs/ROADMAP.md`.
- What ROOKS checked in other open source projects, and their licences: `docs/RESEARCH.md`. Bundled third party components: `THIRD_PARTY_NOTICES.md`.

## Operations

**Upgrading from an update package** (a `rooks-update-*.tar.gz` file plus its `.sha256` file). `scripts/upgrade.sh` makes the upgrade reversible:
- **Checks first.** It verifies the checksum, refuses packages with links, unsafe paths or a `.env`, and shows what will change. Files you edited yourself count as conflicts and stop the upgrade unless you pass `--overwrite`.
- **Backs up.** It saves the code folder (including `.env`) to `~/ai-workspace-backups`, plus a copy of the database if ROOKS is running.
- **Applies, then starts.** It applies the update and runs `./setup.sh`.

`--rollback` puts the previous code back, keeping the replaced folder alongside. Run the script from inside the package so the newest version of it does the work:

```bash
f=~/Downloads/rooks-update-v8.tar.gz
tar xzf "$f" ai-workspace/scripts/upgrade.sh -O > /tmp/rooks-upgrade.sh
bash /tmp/rooks-upgrade.sh --check "$f"   # see what would change
bash /tmp/rooks-upgrade.sh "$f"           # back up, apply, start
bash ~/ai-workspace/scripts/upgrade.sh --rollback   # if you need to go back
```

```bash
docker compose ps                     # status
docker compose logs -f workbench      # logs
docker compose down                   # stop (data is kept in volumes)
./setup.sh                            # update: adds new settings, keeps secrets and data
docker compose pull && docker compose up -d --build   # pull newer images (Paperclip, Open WebUI, Ollama, SearXNG)
```

Data lives in Docker volumes. The core uses only `ai-workspace_workbench` (assistant memory, skills, schedules, company and everything else). Add-ons use `ai-workspace_open-webui`, `ai-workspace_paperclip`, `ai-workspace_hermes`, `ai-workspace_browser` and `ai-workspace_ollama`. Back up `ai-workspace_workbench` plus `.env`, and the volumes of any add-on you use; models can always be downloaded again. Hermes' memory, skills and schedules live in `ai-workspace_hermes`; don't run two Hermes containers against it.

```bash
docker run --rm -v ai-workspace_workbench:/data -v "$PWD":/backup alpine tar czf /backup/workbench.tgz -C /data .
docker run --rm -v ai-workspace_open-webui:/data -v "$PWD":/backup alpine tar czf /backup/open-webui.tgz -C /data .
docker compose stop paperclip && docker run --rm -v ai-workspace_paperclip:/data -v "$PWD":/backup alpine tar czf /backup/paperclip.tgz -C /data . && docker compose start paperclip
```

Pin image versions for predictable upgrades with `HERMES_TAG`, `PAPERCLIP_TAG`, `OPEN_WEBUI_TAG`, `OLLAMA_TAG` and `SEARXNG_TAG` in `.env`. The browser agent pins browser-use to one minor series in `browser-agent/pyproject.toml`; raise it and run `pytest` to upgrade.

## Names that did not change

ROOKS was called AI Workspace before. To keep existing installs, data and integrations working, these technical names stay as they were:

* Docker Compose project `ai-workspace`, so volumes keep their names (`ai-workspace_workbench` and so on), plus the local image tags `ai-workspace/...`.
* The `workbench` service and the `X-Workbench` request header.
* The install folder `~/ai-workspace`, the installer variables `AI_WORKSPACE_DIR`, `AI_WORKSPACE_REPO` and `AI_WORKSPACE_REF`, and the CLI config `~/.config/ai-workspace/cli.json`.
* The repository URL `github.com/Inclined88888/ai-workspace`.
* Environment variables such as `WORKSPACE_NAME`, and the memory export format id `ai-workspace-memories`.

An existing install whose name is still the old default (`AI Workspace`) shows as ROOKS, and an assistant still on the old default name (Atlas) is now called ROOKS; names you chose yourself are kept. With the Open WebUI add-on, its title follows `WORKSPACE_NAME` in `.env`, so change that line to `WORKSPACE_NAME=ROOKS` if you want it renamed there too.

## Development

```bash
cd workbench/backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
DATA_DIR=./data APP_SECRET=$(openssl rand -hex 32) TOOLS_API_KEY=$(openssl rand -hex 32) uvicorn app.main:app --reload
cd workbench/frontend && npm install && npm run dev   # http://localhost:5173, proxies /api to :8000
```

Backend: FastAPI + SQLite. Frontend: React + Vite + TypeScript. The tool server spec is at `/tools/openapi.json`.

## Known limits

- Computer Use on macOS has not been tested on a Mac in this work; run `companion/tests/macos_check.py` on yours first. Visual targeting has only been tested with a stand-in vision model.
- The terminal runner container has not been built with Docker in this work (no Docker daemon in the test environment); the same runner was tested as a plain process.
- Of the seven workflow templates, only spreadsheet cleanup was run end to end against a real file.

- Single user ROOKS (the `openwebui` add-on supports multiple users).
- Telegram is the only built in messaging channel. Discord, Slack, WhatsApp and Signal need the `hermes` add-on.
- Memory extraction and skill saving cost one extra small model call after a conversation. Turn learning off if that matters.
- Calendar: ICS feeds are read only; CalDAV supports reading and creating, not editing or deleting remote events.
- Mail: password/app password IMAP only, no OAuth providers, plain text sending, no attachments.
- Web search needs the `search` add-on or a Brave or Tavily key. Research quality depends on the model and the search results. Verify claims against the sources.
- The Paperclip add-on runs in its own login. ROOKS links to it but does not share a sign in.
- Browser agent results are the agent's own report. Small local models are unreliable at browsing.
