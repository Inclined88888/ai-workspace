# AI Workspace

A self hosted AI workspace you run on your own server with Docker. One place for everyday work: chat and agents, research with sources, writing, notes, tasks, mail and calendar, on local models or any cloud provider.

| App | URL | What it does |
| --- | --- | --- |
| **Workbench** | `http://localhost:3100` | Home dashboard, notes, documents with AI writing assist, tasks, calendar sync, mail, deep research with citations, side by side model comparison |
| **Chat** ([Open WebUI](https://github.com/open-webui/open-webui)) | `http://localhost:3000` | Chat, agents with tools, file uploads and document Q&A (RAG), long term memory, web search, multi model chat |
| Ollama | internal | Runs local models (CPU or NVIDIA GPU) |
| SearXNG | internal | Private metasearch used by Chat and Research |

Chat is wired to Workbench through an OpenAPI tool server, so an agent in Chat can search your notes, add tasks, check your agenda, create events, search mail and save reply drafts.

## Quick start

Requirements: Linux server or desktop with Docker Engine and Docker Compose v2, 8 GB RAM minimum (16 GB recommended), ~15 GB disk plus model sizes. NVIDIA GPU optional.

```bash
git clone <this repo> ai-workspace && cd ai-workspace
./setup.sh
```

`setup.sh` will:

1. Create `.env` (mode 600) and generate every secret.
2. Ask for a workspace name, whether to allow LAN access, the default local model, and optional OpenAI, Anthropic and OpenRouter keys.
3. Turn on the GPU override if `nvidia-smi` works and Docker has the NVIDIA runtime.
4. Build and start the stack, then download the default model (`qwen3:4b`, about 2.5 GB).
5. Print both URLs and a one time **setup code**.

Then:

1. Open **Chat** and create the first account straight away. The first sign up becomes admin; later sign ups wait for your approval.
2. Open **Workbench**, enter the setup code, choose a password, and follow the setup wizard (download models, add API keys).

Run `./setup.sh --yes` for an unattended install with defaults. Re-running setup is safe: it never rotates existing secrets or touches data.

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

## Models and providers

**Local.** Download models in Workbench → Settings → Local models, or `docker compose exec ollama ollama pull qwen3:8b`. Both apps share the same Ollama, so a model pulled once appears in both. Rough memory needs: 4B ≈ 4 GB, 8B ≈ 8 GB, 14B ≈ 12 GB.

**Cloud.** Workbench talks to any OpenAI compatible endpoint (Settings → Model providers, with presets for OpenAI, Anthropic, OpenRouter). Groq, Together, Mistral, LM Studio, vLLM and LiteLLM also work: enter the base URL and key. If a provider does not expose `/models`, type model ids in the Models field.

For Chat, keys entered during `setup.sh` are passed as `OPENAI_API_BASE_URLS`/`OPENAI_API_KEYS`. You can also add connections later in Chat → Admin Settings → Connections. Open WebUI stores settings in its database after first start, so later changes to these env vars are ignored unless you set `ENABLE_PERSISTENT_CONFIG=false`; changing them in the Admin UI is the simplest route.

Anthropic is reached through its [OpenAI SDK compatibility layer](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk). Anthropic describes that layer as intended for testing and comparison, not long term production use, and some features (prompt caching, strict tool schemas) are not available through it.

## Remote access and security

- By default every port binds to `127.0.0.1`. Reach it remotely with an SSH tunnel (`ssh -L 3000:localhost:3000 -L 3100:localhost:3100 you@server`) or a reverse proxy.
- For a public domain put Caddy, Traefik or nginx with HTTPS in front, set `WEBUI_PUBLIC_URL` to the https URL and `COOKIE_SECURE=true`, then `docker compose up -d`.
- Workbench is a single owner app. Passwords are hashed with scrypt; mail, CalDAV and provider credentials are encrypted at rest with a key derived from `APP_SECRET`. **Back up `.env`**: if you lose `APP_SECRET`, stored credentials cannot be decrypted and must be re-entered.
- Ollama and SearXNG are not published to the host.
- The tool server requires `TOOLS_API_KEY` as a bearer token. Its OpenAPI description is readable without the key (it contains no data).

## Operations

```bash
docker compose ps                     # status
docker compose logs -f workbench      # logs
docker compose down                   # stop (data is kept in volumes)
docker compose pull && docker compose up -d --build   # update
```

Data lives in Docker volumes `ai-workspace_open-webui`, `ai-workspace_workbench` and `ai-workspace_ollama`. Back up the first two plus `.env`; models can always be downloaded again.

```bash
docker run --rm -v ai-workspace_workbench:/data -v "$PWD":/backup alpine tar czf /backup/workbench.tgz -C /data .
docker run --rm -v ai-workspace_open-webui:/data -v "$PWD":/backup alpine tar czf /backup/open-webui.tgz -C /data .
```

Pin image versions for predictable upgrades with `OPEN_WEBUI_TAG`, `OLLAMA_TAG` and `SEARXNG_TAG` in `.env`.

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
