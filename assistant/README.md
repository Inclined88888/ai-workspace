# Personal assistant (always on, cheap server)

A personal AI assistant that learns and grows with you, running 24/7 on a small cloud server. It is [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research (MIT), used unmodified from its official Docker image. This folder adds a one command setup and a `./hermes` command that makes the everyday tasks one line each.

What you get:

| You asked for | How it works here |
| --- | --- |
| Remembers past conversations | Every session is stored and full text searchable; it looks back on its own when relevant |
| Builds a model of who you are | Persistent memory about you and your work, kept across sessions and across Terminal, Telegram and Discord |
| Creates its own skills | After a complex task it can save the procedure as a reusable skill and improve it on later use |
| Any model, switch with a key | `./hermes key openrouter` then `./hermes use openrouter <model>`; memory, skills and schedules carry over |
| Polished terminal interface | `./hermes` opens Hermes' full terminal UI (multiline editing, slash commands, streaming tool output) |
| Telegram and Discord | `./hermes connect telegram` / `discord`; only your own account is allowed |
| Always on, cheap | One container, about 430 MB of RAM idle in testing; no GPU, no open ports |
| Scheduled tasks | `./hermes daily-report 08:00 telegram`, or just ask: "every Friday at 5pm summarise my week" |

## Server

Any Linux VPS with Docker works. Measured in testing: the container uses about 430 MB of RAM idle (with the dashboard on) and the image is about 4.3 GB. A **2 GB RAM / 20 GB disk** server is comfortable. 1 GB is tight and untested; if you try it, turn the dashboard off (`HERMES_DASHBOARD=0` in `.env`) and add swap. The model itself runs at your provider, so no GPU is needed.

1. Create the server (Ubuntu or Debian), log in over **SSH** (browser consoles can corrupt pasted keys).
2. Install Docker: `curl -fsSL https://get.docker.com | sh`, then `sudo usermod -aG docker $USER` and log in again.
3. Copy this `assistant` folder to the server (or clone the repo) and run:

```bash
cd assistant
./setup.sh
```

Setup asks for: provider (Anthropic, OpenAI or OpenRouter) and its API key, your timezone, and optionally Telegram, Discord and a daily report. Everything else has safe defaults. Re-running it is safe.

No ports are opened to the internet. Telegram and Discord work through outbound connections, and the dashboard listens on the server's localhost only.

## Everyday use

```bash
./hermes                                  # chat in the terminal UI
./hermes key anthropic                    # store a key (prompted, hidden)
./hermes use anthropic                    # switch model (default claude-sonnet-5-5)
./hermes use openrouter openai/gpt-6-luna # any OpenRouter model id
./hermes use openai                       # asks for the OpenAI model id
./hermes connect telegram                 # bot token from @BotFather + your user id
./hermes connect discord                  # bot token + your user id (+ optional report channel)
./hermes daily-report 08:00 telegram      # daily briefing to your Telegram chat
./hermes cron list                        # see, pause or remove schedules (cron pause/resume/remove ID)
./hermes doctor                           # health check
```

Anything else is passed straight to Hermes, so its full CLI works: `./hermes skills list`, `./hermes cron create "0 18 * * 5" "Summarise my week"`, `./hermes model` (interactive picker).

Scheduling in plain language also works from any chat: "remind me in 30 minutes", "every weekday at 9 check Hacker News for AI news and send me the top 5". Schedules follow the timezone you set (`./hermes config set timezone Europe/London`).

Dashboard (memory, skills, sessions, schedules, keys, messaging): from your laptop run `ssh -L 9119:localhost:9119 you@server`, open http://localhost:9119, user `admin`, password in `.env` (`HERMES_DASHBOARD_PASSWORD`).

## Things to know

* **Model choice matters.** Memory, skills and scheduled reports are only as good as the model. In testing, a tiny 0.6B local model ran the daily report job end to end but wrote a template full of `[DATE]` style placeholders. Use a capable cloud model for the assistant.
* **Context window.** Hermes requires models with at least a 64K token window and refuses smaller ones with a clear message.
* **Access control.** Hermes denies everyone by default. `./hermes connect` allows only the user id you enter. Never set `GATEWAY_ALLOW_ALL_USERS` on a bot people can find.
* **It can act.** Hermes has a terminal and file tools inside its container. It cannot touch the host beyond its data volume, but anything you give it a key for (email, GitHub, ...) it can use.
* **Costs.** Every scheduled run is a paid model call, and agent runs can use several calls and many tokens. Start with one daily job, check your provider's usage page after a few days, and review `./hermes cron list` occasionally.
* **Privacy.** Your conversations go to the model provider you choose. Memory and skills stay on your server.

## Backup, upgrade, move

```bash
# Back up everything (memory, skills, sessions, schedules, keys)
docker run --rm -v hermes-assistant_hermes-data:/d -v "$PWD":/b alpine tar czf /b/hermes-backup.tgz -C /d .
# Upgrade (data is kept in the volume)
docker compose pull && docker compose up -d
```

To move servers, restore the backup into the new server's `hermes-assistant_hermes-data` volume before the first start.

## Already running the full ROOKS workspace?

You don't need this folder: the main stack already includes Hermes (wired to ROOKS and the browser agent). The same `./hermes` command works from the repository root, with one extra option: `./hermes use ollama qwen3:4b` for a local model.
