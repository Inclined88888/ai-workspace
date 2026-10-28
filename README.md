# AI Workspace

A self hosted AI workspace in **one light container**. Chat with an assistant that remembers you and learns skills, plus notes, documents, tasks, calendar, mail, research with sources, scheduled reports, a company of AI agents with budgets, and model comparison. Runs on Anthropic, OpenAI, OpenRouter or any OpenAI compatible provider, or local models.

**The core is Workbench:** FastAPI + SQLite + React, one image (about 350 MB), about 100 MB of RAM at idle (measured on the backend process; real use depends on traffic). No GPU, no database server, no extra containers. A small VPS with 1 GB of RAM or a Mac laptop runs it.

Everything heavier is an **optional add-on, off by default**:

| Add-on | Turn on with | Adds | Cost |
| --- | --- | --- | --- |
| `search` | SearXNG | Dependable free private web search (without it: a Brave or Tavily key, or DuckDuckGo's keyless page as a fallback) | small |
| `browser` | browser-use + Chromium | Browser agent page and a `browse_web` tool | ≈ 2.5 GB image |
| `local-models` | Ollama in Docker | Local models (on a Mac, native Ollama is faster and is detected by setup) | ≈ 8.5 GB image + models |
| `openwebui` | Open WebUI | A second chat app with RAG knowledge bases and multi user accounts | ≈ 6.5 GB image |
| `paperclip` | Paperclip | Upstream company of agents running coding CLIs (Claude Code, Codex, OpenCode) | ≈ 13 GB image |
| `hermes` | Hermes Agent | Upstream personal agent with its own gateway (WhatsApp, Signal, Slack) | ≈ 4.3 GB image |

You don't need any of them for the assistant, memory, skills, schedules, Telegram or the company of agents. Those are built into Workbench.

## Quick start

Requirements: Docker with Compose v2 (Docker Desktop or OrbStack on a Mac), 1 GB RAM free, about 2 GB of disk for the build. At least one model provider key, or Ollama.

One command (Linux or macOS):

```bash
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install.sh | bash
```

With add-ons, or with no questions:

```bash
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install.sh | bash -s -- --with search,browser
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install.sh | bash -s -- --yes
```

The installer checks Docker (on Linux it offers to install it with Docker's official script; on a Mac install Docker Desktop or OrbStack first), downloads the code to `~/ai-workspace` (`AI_WORKSPACE_DIR` changes that), and runs `setup.sh`. Run the same command again to upgrade. Prefer to read it first? `curl -fsSL .../install.sh -o install.sh`, read it, then `bash install.sh`.

From a clone instead:

```bash
git clone https://github.com/Inclined88888/ai-workspace && cd ai-workspace
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
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install.sh | bash
```

Native Ollama uses the Mac GPU; the `local-models` add-on would run on CPU inside Docker's VM, so skip it on a Mac.

## The assistant

**Assistant** is the main page: one chat that can use every part of the workspace.

* **Tools.** Notes (search, read, create, append), tasks, calendar (agenda, events, create event), mail (search, read, save draft only, never send), web search and page reading, the browser agent when that add-on is on, memory, skills and schedules. Each tool step shows live in the chat.
* **Prompt injection guard.** Web pages and email are written by other people and can carry instructions aimed at the assistant. Once a turn has read any (web search, a page, the browser agent, email), it cannot save memories, skills or schedules for the rest of that turn; it tells you what it wanted to do and you confirm in a new message. Text from web pages and email is also never shown to the background skill writer. This narrows the risk; it does not remove it. Read what the assistant did, especially with the browser agent, which can still click and fill forms.
* **Memory.** After a conversation it quietly extracts durable facts about you (profile, preferences, facts), at most 5 per conversation, skipping duplicates. Relevant ones are injected into every new conversation. You can also say "remember that…" or "forget…". Review and edit everything under **Memory & skills**. Turn learning off in Assistant settings.
* **Skills.** When a task took 3 or more tool calls, it can save the procedure as a named skill and improve it on later use. Skills are listed to the model and loaded on demand, so they cost almost nothing until used.
* **History.** Every conversation is full text searchable, by you and by the assistant (`search_history`).
* **Models.** Switch per conversation from the model menu. Any provider in Settings works.
* **Terminal.** `cli/ws` is a zero dependency Python CLI: `cli/ws login`, then `cli/ws` for a REPL or `cli/ws "what's on today?"` for one shot. Also `cli/ws memory`, `skills`, `inbox`, `schedules`.
* **Telegram.** Assistant settings → Telegram: paste a bot token from @BotFather and your numeric user id. Only allowlisted ids get answers; others are told their id so you can add them. `/new` starts a fresh conversation. The token is stored encrypted.

**Automations.** Cron schedules in your timezone ("Daily report, 8:00" is one click). Results go to the Inbox and optionally Telegram. You can also ask in chat: "every Friday at 5pm summarise my week". Scheduled runs cannot create more schedules. The next run is booked before each run, so a failing job cannot loop.

**Company.** Set a mission, hire agents (CEO, CTO, researcher…) with a model, instructions, who they report to, a **monthly budget in dollars** and a **monthly token limit**. Give a task to any agent; managers can `delegate_task` to their reports (up to 3 levels deep). The org chart shows spend bars; the board shows To do, Queued, Running, Done and Needs you. An agent over its limit is **paused automatically** until you raise the limit or the month rolls over. Limits are checked before every model call, so spend can overshoot by at most one call. OpenRouter reports real cost; for other providers set your price per 1M tokens in Settings → Usage, otherwise dollar budgets cannot be enforced (token limits always are).

## Workspace features

**Second chat app (`openwebui` add-on).** Streaming chat with any connected model; file uploads and knowledge bases with retrieval; memory (Settings → Personalization); web search through SearXNG when the `search` add-on is on (toggle per chat); multi model chat (pick several models in the model selector to get answers side by side); custom models with system prompts and tools.

**Workbench tools in Open WebUI.** The `Workbench` tool server is preconfigured. Enable it per chat from the tools button in the message box, or attach it to a model in Admin → Models. Tools: `search_notes`, `read_note`, `create_note`, `append_to_note`, `list_tasks`, `create_task`, `update_task`, `get_agenda`, `list_events`, `create_event`, `list_calendars`, `search_email`, `read_email`, `save_email_draft`, `web_search`. There is deliberately **no send email tool**: agents can only save drafts, which you review and send under Mail → Drafts. Tool calling quality depends on the model; small local models are hit and miss, larger ones (Qwen3 8B+, or cloud models) are reliable.

**Research.** Ask a question; the model plans several searches, Workbench queries your search provider (SearXNG add-on, or a Brave or Tavily key in Assistant settings), reads the pages (internal and private network addresses are refused), and the model writes a Markdown report with numbered inline citations and a source list. Save any report to Documents.

**Compare.** Up to four columns, each with its own model and conversation. One prompt goes to all of them; each column shows total time, time to first token, tokens and tokens per second.

**Notes and documents.** Markdown with live preview, tags, pinning, full text search (SQLite FTS5) and autosave. AI assist works on the selection or the whole text: improve, fix grammar, shorten, expand, summarize, continue, outline, or a free form instruction. You see the result before it replaces anything.

**Tasks.** Due dates, priorities, projects, grouping by Overdue / Today / Upcoming, and "break down a goal" which turns one sentence into a task list.

**Calendar.** Month view plus day agenda. Sources:
- **ICS subscription** (read only): Google Calendar "Secret address in iCal format", Outlook published ICS, any `.ics`/`webcal://` link. Refreshes every 15 minutes.
- **CalDAV** (read and create): Nextcloud, Fastmail, iCloud (`https://caldav.icloud.com` with an app specific password), Radicale, Baïkal. Use *Find calendars* to pick one.
- **Local** calendar stored in Workbench.

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
* **Tools:** Workbench (13 tools: notes, tasks, calendar, mail search and drafts) and the browser agent are attached as MCP servers. Hermes cannot send mail, only draft it.
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

* **Workbench → Browser agent:** pick the provider and model, a step limit, and optionally lock it to specific sites. Watch the steps and a live screenshot.
* **Chat:** the `Browser` tool (`browse_web`, `read_page`) is preconfigured; enable it per chat like the Workbench tool.
* **Paperclip employees** (OpenCode) and **Hermes** have it as the `browser` MCP server automatically.
* **Standalone:** `browser-agent/` is a normal Python package with a CLI (`browser-agent run`, `open`, `click`, `type`, `screenshot`, `text`, `links`) and an `examples/` folder. See [browser-agent/README.md](browser-agent/README.md).

Model choice: `BROWSER_LLM_PROVIDER=auto` uses Anthropic if `ANTHROPIC_API_KEY` is set, else Gemini if `GEMINI_API_KEY` is set, else local Ollama. Claude and Gemini are far more reliable at browsing than small local models; in testing a 0.6B local model invented an answer and claimed success, which is why the UI labels results as the agent's own claim.

**Cost control.** Browser runs started from Chat or by Paperclip employees are paid with the browser service's model key, **outside Paperclip's budgets**. Three limits apply to every caller: `max_steps` per run, `BROWSER_MAX_STEPS_LIMIT` (default 50) as the ceiling for any run, and `BROWSER_MONTHLY_STEP_LIMIT` (default 2000 steps per UTC month, 0 = unlimited). Workbench shows this month's usage. For guaranteed $0, set `BROWSER_LLM_PROVIDER=ollama`.

**Safety.** The service refuses IP address URLs and the stack's own hostnames, and its one-off page reads refuse any host that resolves to a private address. That keeps it off your containers and LAN. A public hostname that resolves to a private address could still be reached during an agent run; don't rely on this as your only network boundary on a sensitive LAN. The agent can fill forms and click buttons on public sites, so be specific about what it must not do (buy, post, delete).

## Models and providers

**Local.** With native Ollama: `ollama pull qwen3:8b`. With the `local-models` add-on: Workbench → Settings → Local models, or `docker compose exec ollama ollama pull qwen3:8b`. Rough memory needs for the weights: 4B ≈ 4 GB, 8B ≈ 8 GB, 14B ≈ 12 GB, plus the context window below.

**Context window.** Ollama's own default is 4096 tokens. The assistant's tool list, your memories and one web page together are larger than that, and Ollama cuts what does not fit, so tool use quietly breaks. Workbench therefore talks to Ollama through its native API and asks for 16K tokens (Settings → General → Local model context window; `OLLAMA_CONTEXT_LENGTH` in `.env` sets the default). The `local-models` add-on also starts Ollama with that default for Open WebUI and the browser agent. Native Ollama apps ignore `.env`: if you use Open WebUI or the browser agent with native Ollama, set `OLLAMA_CONTEXT_LENGTH=16384` where `ollama serve` runs. A bigger window needs more RAM, roughly 1.2 GB per extra 8K tokens for Qwen3 4B or 8B with an unquantized cache. Hermes asks for its own 64K window; when Hermes and Workbench share one Ollama, the model reloads whenever they alternate.

**Free.** Three ways to run at $0, all one click in Settings → Model providers (free models show "(free)" in every model menu):

* **Ollama** on your own machine. Fully private.
* **OpenRouter** with a free account key: every model ending in `:free` (about 17 at the time of writing, most with tool calling). Limits: 20 requests a minute and 50 a day, or 1000 a day once you have bought $10 of credits.
* **Google Gemini** free tier with a key from aistudio.google.com (Flash and Flash-Lite models). Google may use free tier prompts to improve its products.

**OpenCode.** OpenCode's free models (Big Pickle, the `-free` models) only work inside the OpenCode app; called from anything else they answer "Free models work in OpenCode". So Workbench offers **OpenCode Go** (flat $10 a month) and **OpenCode Zen** (pay as you go) as providers, and hides the models those endpoints can't serve. For OpenCode's free models, use the `paperclip` add-on: its agents run inside OpenCode itself.

**Cloud.** Workbench talks to any OpenAI compatible endpoint (Settings → Model providers, with presets for OpenAI, Anthropic, OpenRouter). Groq, Together, Mistral, LM Studio, vLLM and LiteLLM also work: enter the base URL and key. If a provider does not expose `/models`, type model ids in the Models field.

For Chat, keys entered during `setup.sh` are passed as `OPENAI_API_BASE_URLS`/`OPENAI_API_KEYS`. You can also add connections later in Chat → Admin Settings → Connections. Open WebUI stores settings in its database after first start, so later changes to these env vars are ignored unless you set `ENABLE_PERSISTENT_CONFIG=false`; changing them in the Admin UI is the simplest route.

Anthropic is reached through its [OpenAI SDK compatibility layer](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk). Anthropic describes that layer as intended for testing and comparison, not long term production use, and some features (prompt caching, strict tool schemas) are not available through it.

## Remote access and security

- By default every port binds to `127.0.0.1`. Reach it remotely with an SSH tunnel (`ssh -L 3000:localhost:3000 -L 3100:localhost:3100 you@server`) or a reverse proxy.
- For a public domain put Caddy, Traefik or nginx with HTTPS in front, set `WEBUI_PUBLIC_URL` and `PAPERCLIP_PUBLIC_URL` to the https URLs and `COOKIE_SECURE=true`, then `docker compose up -d`. Set `FORWARDED_ALLOW_IPS` to the proxy's address as Workbench sees it, so login throttling sees real client addresses; X-Forwarded-For from anyone else is ignored. Paperclip only answers on hostnames in `PAPERCLIP_PUBLIC_URL` and `PAPERCLIP_ALLOWED_HOSTNAMES`.
- Remote access from your phone: a VPN such as Tailscale is the simplest safe option. Add the Tailscale hostname to `PAPERCLIP_ALLOWED_HOSTNAMES`.
- Workbench is a single owner app. Passwords are hashed with scrypt; mail, CalDAV and provider credentials are encrypted at rest with a key derived from `APP_SECRET`. **Back up `.env`**: if you lose `APP_SECRET`, stored credentials cannot be decrypted and must be re-entered.
- Ollama, SearXNG, the browser agent and Hermes' API are not published to the host. The Hermes dashboard is (port 3300, basic auth, bound to `BIND_ADDRESS`); it stores API keys, so keep it on localhost or behind a VPN.
- Hermes can run terminal commands inside its own container. Its tool approvals and loop guards apply, but treat it as having full access to its volume and whatever its tools reach. The browser agent requires `BROWSER_API_KEY` on every request, including MCP.
- The tool server requires `TOOLS_API_KEY` as a bearer token. Its OpenAPI description is readable without the key (it contains no data).

## Operations

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

## Development

```bash
cd workbench/backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
DATA_DIR=./data APP_SECRET=$(openssl rand -hex 32) TOOLS_API_KEY=$(openssl rand -hex 32) uvicorn app.main:app --reload
cd workbench/frontend && npm install && npm run dev   # http://localhost:5173, proxies /api to :8000
```

Backend: FastAPI + SQLite. Frontend: React + Vite + TypeScript. The tool server spec is at `/tools/openapi.json`.

## Known limits

- Single user Workbench (the `openwebui` add-on supports multiple users).
- Telegram is the only built in messaging channel. Discord, Slack, WhatsApp and Signal need the `hermes` add-on.
- Memory extraction and skill saving cost one extra small model call after a conversation. Turn learning off if that matters.
- Calendar: ICS feeds are read only; CalDAV supports reading and creating, not editing or deleting remote events.
- Mail: password/app password IMAP only, no OAuth providers, plain text sending, no attachments.
- Web search works with no setup through DuckDuckGo's keyless results page, but that page is meant for people: it rate limits and can change without notice, and automated use may conflict with DuckDuckGo's terms. Turn it off in Assistant settings if that matters to you. For dependable search use the `search` add-on (SearXNG) or a Brave or Tavily key. Research quality depends on the model and the search results. Verify claims against the sources.
- The Paperclip add-on runs in its own login. Workbench links to it but does not share a sign in.
- Browser agent results are the agent's own report. Small local models are unreliable at browsing.
