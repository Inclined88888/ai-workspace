# AI Workspace

A self hosted AI workspace you run on your own server with Docker. One place for everyday work: chat and agents, research with sources, writing, notes, tasks, mail and calendar, a browser agent, and a company of AI employees with budgets. Runs on local models, free OpenCode models, or any cloud provider.

| App | URL | What it does |
| --- | --- | --- |
| **Workbench** | `http://localhost:3100` | Home dashboard, notes, documents with AI writing assist, tasks, calendar sync, mail, deep research with citations, **browser agent**, side by side model comparison |
| **Chat** ([Open WebUI](https://github.com/open-webui/open-webui)) | `http://localhost:3000` | Chat, agents with tools, file uploads and document Q&A (RAG), long term memory, web search, multi model chat |
| **Company** ([Paperclip](https://github.com/paperclipai/paperclip)) | `http://localhost:3200` | You are the board: set the mission, hire AI agents as CEO, CTO, engineers, see the org chart, assign tasks, track progress and cost, monthly budget per agent |
| **Hermes** ([Hermes Agent](https://github.com/NousResearch/hermes-agent)) | `http://localhost:3300` (dashboard) | A personal agent that learns: persistent memory, skills it writes for itself, search over past conversations, scheduled jobs, subagents, and messaging (Telegram, Discord, Slack and more). Also appears in Chat as the model `hermes-agent` |
| Browser agent ([browser-use](https://github.com/browser-use/browser-use) + Playwright) | internal | Plain English goals become real browser actions. Used by Workbench, Chat and Paperclip employees. Also a standalone Python package and CLI in `browser-agent/` |
| Ollama | internal | Runs local models (CPU or NVIDIA GPU) |
| SearXNG | internal | Private metasearch used by Chat and Research |

How they connect:

* Chat can use Workbench (notes, tasks, calendar, mail drafts) and the browser agent as tools, and can talk to Hermes as a model.
* Workbench and the browser agent are also MCP servers, so Hermes and Paperclip's OpenCode employees can use your notes, tasks, calendar, mail drafts and the browser.
* Paperclip employees running on OpenCode get your local Ollama models, OpenCode's free tier models, and the browser agent (over MCP) out of the box.
* Workbench links to Chat and Paperclip and has its own Browser page for running and watching browser jobs.

Paperclip, Hermes and browser-use are used **unmodified** from upstream (published Docker images, browser-use from PyPI). Everything this project adds sits around them, so upgrading is a version bump, not a merge.

## Quick start

Requirements: Linux server or desktop with Docker Engine and Docker Compose v2, 16 GB RAM recommended (8 GB works without Paperclip agents running locally), **about 40 GB of disk for the images** (Paperclip ≈ 13 GB, Ollama ≈ 8.5 GB, Open WebUI ≈ 6.5 GB, Hermes ≈ 4.3 GB, browser agent ≈ 2.5 GB) plus model sizes. NVIDIA GPU optional.

```bash
git clone <this repo> ai-workspace && cd ai-workspace
./setup.sh
```

`setup.sh` will:

1. Create `.env` (mode 600) and generate every secret.
2. Ask for a workspace name, whether to allow LAN access, the default local model, and optional OpenAI, Anthropic, OpenRouter, Gemini and OpenCode Zen keys.
3. Turn on the GPU override if `nvidia-smi` works and Docker has the NVIDIA runtime.
4. Build and start the stack, then download the default model (`qwen3:4b`, about 2.5 GB).
5. Give Paperclip's OpenCode every downloaded Ollama model and the browser tool (`scripts/sync-opencode-config.sh`).
6. Point Hermes at your model and give it the Workbench and browser tools (`scripts/hermes-config.sh`, first run only).
7. Print the URLs, a one time Workbench **setup code**, a one time **Paperclip admin invite link** and the **Hermes dashboard login**.

Then, straight away (first come, first admin):

1. Open **Chat** and create the first account. The first sign up becomes admin; later sign ups wait for approval.
2. Open the **Paperclip invite link** and create your account. You become the board. Lost the link or it expired (72 h)? `./scripts/paperclip-invite.sh` prints a new one while no admin exists.
3. Open **Workbench**, enter the setup code, choose a password, and follow the setup wizard.

Run `./setup.sh --yes` for an unattended install with defaults. Re-running setup is safe and is also how you **upgrade**: it adds any new settings to `.env`, generates missing secrets, never rotates existing ones and never touches data.

## Features

**Chat and agents (Open WebUI).** Streaming chat with any connected model; file uploads and knowledge bases with retrieval; memory (Settings → Personalization); web search through the bundled SearXNG (toggle per chat); multi model chat (pick several models in the model selector to get answers side by side); custom models with system prompts and tools.

**Workbench tools in Chat.** The `Workbench` tool server is preconfigured. Enable it per chat from the tools button in the message box, or attach it to a model in Admin → Models. Tools: `search_notes`, `read_note`, `create_note`, `append_to_note`, `list_tasks`, `create_task`, `update_task`, `get_agenda`, `list_events`, `create_event`, `list_calendars`, `search_email`, `read_email`, `save_email_draft`, `web_search`. There is deliberately **no send email tool**: agents can only save drafts, which you review and send under Mail → Drafts. Tool calling quality depends on the model; small local models are hit and miss, larger ones (Qwen3 8B+, or cloud models) are reliable.

**Research.** Ask a question; the model plans several searches, Workbench queries SearXNG, reads the pages (internal and private network addresses are refused), and the model writes a Markdown report with numbered inline citations and a source list. Save any report to Documents.

**Compare.** Up to four columns, each with its own model and conversation. One prompt goes to all of them; each column shows total time, time to first token, tokens and tokens per second.

**Notes and documents.** Markdown with live preview, tags, pinning, full text search (SQLite FTS5) and autosave. AI assist works on the selection or the whole text: improve, fix grammar, shorten, expand, summarize, continue, outline, or a free form instruction. You see the result before it replaces anything.

**Tasks.** Due dates, priorities, projects, grouping by Overdue / Today / Upcoming, and "break down a goal" which turns one sentence into a task list.

**Calendar.** Month view plus day agenda. Sources:
- **ICS subscription** (read only): Google Calendar "Secret address in iCal format", Outlook published ICS, any `.ics`/`webcal://` link. Refreshes every 15 minutes.
- **CalDAV** (read and create): Nextcloud, Fastmail, iCloud (`https://caldav.icloud.com` with an app specific password), Radicale, Baïkal. Use *Find calendars* to pick one.
- **Local** calendar stored in Workbench.

**Mail.** IMAP/SMTP with app passwords: Fastmail, iCloud, Gmail (needs 2 step verification and an app password), and most self hosted servers. Read, search, reply, compose, and "Draft with AI" with your intent. AI drafts use `[placeholders]` instead of inventing facts; every send asks for confirmation. Outlook.com and Microsoft 365 are **not** supported because Microsoft requires OAuth for IMAP.

## Company: Paperclip

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

## Hermes Agent

Hermes is the agent that gets better the longer you use it. What it adds over the other agents here:

* **Learning loop.** After a task it can write a reusable skill (a saved procedure) and improve it on later use. Skills live in its volume and show up in the dashboard.
* **Memory that persists.** It keeps notes about you and your work across sessions, and full text search over its past conversations.
* **Scheduled jobs.** "Every weekday at 8, check my agenda and overdue tasks and message me a summary" runs unattended with its built in scheduler.
* **Reach it anywhere.** Telegram, Discord, Slack, WhatsApp, Signal and more from one gateway. Easiest setup: dashboard → Messaging → Telegram → Create with QR, which also restricts the bot to your own Telegram user.
* **Subagents** for parallel work, context compression for long sessions, and loop guards that stop runaway tool calls when nobody is watching.

How it is wired here:

* **Chat:** pick the model `hermes-agent` (new installs; see the note below for existing ones).
* **Tools:** Workbench (13 tools: notes, tasks, calendar, mail search and drafts) and the browser agent are attached as MCP servers. Hermes cannot send mail, only draft it.
* **Dashboard** at port 3300 (user `admin`, password printed by `setup.sh`, stored as `HERMES_DASHBOARD_PASSWORD` in `.env`): memory, skills, scheduled jobs, sessions, model and messaging setup.
* **Model:** `scripts/hermes-config.sh` picks Anthropic if `ANTHROPIC_API_KEY` is set, else OpenRouter, else your local `DEFAULT_MODEL`. It runs once; afterwards change the model in the dashboard. `--force` reapplies the defaults.

Requirements and limits, verified in testing:

* **Hermes refuses models with less than a 64K context window.** `qwen3:4b` (the default, 256K) qualifies; `qwen3:8b` and `qwen3:0.6b` (40K) do not. For local models the window is pinned to 64K (`model.ollama_num_ctx`), because by default Hermes asks Ollama for the model's full window, and for `qwen3:4b` that cache alone would need tens of GB of RAM. Even at 64K, plan on roughly 8 to 12 GB of RAM for Hermes on a local 4B model.
* **Small local models can chat but not drive tools.** In testing, a 0.6B model answered a plain question (70 s on CPU) but called tools with malformed arguments until Hermes' loop guard stopped it. Use a cloud model or at least `qwen3:4b` on a GPU for agent work.
* **Existing installs:** Open WebUI stores connections in its database after first start, so on an install that predates Hermes, add it in Chat → Admin Settings → Connections: URL `http://hermes:8642/v1`, key `HERMES_API_KEY` from `.env`.
* **Hiring Hermes in Paperclip** (adapter "Hermes Gateway", API URL `http://hermes:8642`, key `HERMES_API_KEY`, Paperclip API URL `http://paperclip:3100`) is **not preconfigured**. Paperclip refuses plain HTTP to any host other than localhost. Your two options: put TLS in front of Hermes, or tick Paperclip's `dangerouslyAllowInsecureRemoteHttp` for that agent, accepting that the traffic is unencrypted on the Docker network between the two containers (it never leaves the machine). That is your call; this setup does not set it for you.
* Hermes' own browser tools are disabled in this image (no browser installed); it uses the browser agent over MCP instead.

## Browser agent

Give a goal in plain English, for example "Go to weworkremotely.com and find three remote software engineer roles" or "On PCPartPicker find a compatible CPU, motherboard and RAM for an AM5 build". The agent opens a headless Chromium, decides each step with your chosen model, and reports back with the pages it visited.

* **Workbench → Browser agent:** pick the provider and model, a step limit, and optionally lock it to specific sites. Watch the steps and a live screenshot.
* **Chat:** the `Browser` tool (`browse_web`, `read_page`) is preconfigured; enable it per chat like the Workbench tool.
* **Paperclip employees** (OpenCode) and **Hermes** have it as the `browser` MCP server automatically.
* **Standalone:** `browser-agent/` is a normal Python package with a CLI (`browser-agent run`, `open`, `click`, `type`, `screenshot`, `text`, `links`) and an `examples/` folder. See [browser-agent/README.md](browser-agent/README.md).

Model choice: `BROWSER_LLM_PROVIDER=auto` uses Anthropic if `ANTHROPIC_API_KEY` is set, else Gemini if `GEMINI_API_KEY` is set, else local Ollama. Claude and Gemini are far more reliable at browsing than small local models; in testing a 0.6B local model invented an answer and claimed success, which is why the UI labels results as the agent's own claim.

**Cost control.** Browser runs started from Chat or by Paperclip employees are paid with the browser service's model key, **outside Paperclip's budgets**. Three limits apply to every caller: `max_steps` per run, `BROWSER_MAX_STEPS_LIMIT` (default 50) as the ceiling for any run, and `BROWSER_MONTHLY_STEP_LIMIT` (default 2000 steps per UTC month, 0 = unlimited). Workbench shows this month's usage. For guaranteed $0, set `BROWSER_LLM_PROVIDER=ollama`.

**Safety.** The service refuses IP address URLs and the stack's own hostnames, and its one-off page reads refuse any host that resolves to a private address. That keeps it off your containers and LAN. A public hostname that resolves to a private address could still be reached during an agent run; don't rely on this as your only network boundary on a sensitive LAN. The agent can fill forms and click buttons on public sites, so be specific about what it must not do (buy, post, delete).

## Models and providers

**Local.** Download models in Workbench → Settings → Local models, or `docker compose exec ollama ollama pull qwen3:8b`. Both apps share the same Ollama, so a model pulled once appears in both. Rough memory needs: 4B ≈ 4 GB, 8B ≈ 8 GB, 14B ≈ 12 GB.

**Cloud.** Workbench talks to any OpenAI compatible endpoint (Settings → Model providers, with presets for OpenAI, Anthropic, OpenRouter). Groq, Together, Mistral, LM Studio, vLLM and LiteLLM also work: enter the base URL and key. If a provider does not expose `/models`, type model ids in the Models field.

For Chat, keys entered during `setup.sh` are passed as `OPENAI_API_BASE_URLS`/`OPENAI_API_KEYS`. You can also add connections later in Chat → Admin Settings → Connections. Open WebUI stores settings in its database after first start, so later changes to these env vars are ignored unless you set `ENABLE_PERSISTENT_CONFIG=false`; changing them in the Admin UI is the simplest route.

Anthropic is reached through its [OpenAI SDK compatibility layer](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk). Anthropic describes that layer as intended for testing and comparison, not long term production use, and some features (prompt caching, strict tool schemas) are not available through it.

## Remote access and security

- By default every port binds to `127.0.0.1`. Reach it remotely with an SSH tunnel (`ssh -L 3000:localhost:3000 -L 3100:localhost:3100 you@server`) or a reverse proxy.
- For a public domain put Caddy, Traefik or nginx with HTTPS in front, set `WEBUI_PUBLIC_URL` and `PAPERCLIP_PUBLIC_URL` to the https URLs and `COOKIE_SECURE=true`, then `docker compose up -d`. Paperclip only answers on hostnames in `PAPERCLIP_PUBLIC_URL` and `PAPERCLIP_ALLOWED_HOSTNAMES`.
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

Data lives in Docker volumes `ai-workspace_open-webui`, `ai-workspace_workbench`, `ai-workspace_paperclip`, `ai-workspace_hermes`, `ai-workspace_browser` and `ai-workspace_ollama`. Back up the first four plus `.env`; models can always be downloaded again. Hermes' memory, skills and schedules live in `ai-workspace_hermes`; don't run two Hermes containers against it.

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

- Single user Workbench (Chat supports multiple users).
- Calendar: ICS feeds are read only; CalDAV supports reading and creating, not editing or deleting remote events.
- Mail: password/app password IMAP only, no OAuth providers, plain text sending, no attachments.
- Research quality depends on the model and on what SearXNG's upstream engines return. Verify claims against the sources.
- Paperclip runs in its own login. Workbench links to it but does not share a sign in.
- Browser agent results are the agent's own report. Small local models are unreliable at browsing.
