# Launch posts

Drafts for launch announcements. Everything below describes **shipped** features only, except where a roadmap item is explicitly marked **(Phase N)**. Do not publish a roadmap item as a current feature.

The full source list lives in [docs/README.md](../docs/README.md) ("100 reasons people pick AI Workspace").

## 1. Show HN

**Show HN: AI Workspace — a self-hosted AI workspace in one ~350 MB container**

I got tired of "self-hosted AI" meaning six containers, a vector database, and 8 GB of RAM before you get anything useful. AI Workspace is one container: FastAPI + SQLite + React, ~350 MB image, ~100 MB RAM at idle (measured on the backend process). It runs fine on a €4 VPS, no GPU needed.

It's a workspace, not just an agent runtime. One app (Workbench) includes:

- An assistant with tools that can act on your notes, tasks, calendar, and mail — not just chat about them
- Notes, tasks, calendar (ICS/CalDAV), and mail (IMAP/SMTP, **draft-only** — it never sends)
- Research with citations and side-by-side model comparison
- Full-text searchable history across every conversation

Memory that earns its keep: after each turn it extracts at most 5 durable facts (40-char minimum, dedup built in), and recall is plain FTS5 bm25 — no vector DB. You can review, edit, or switch memory off entirely.

Safety is by construction, not by prompt: secrets encrypted at rest (Fernet), no shell tool by default, a 12-step tool limit per turn, scheduled runs can't create schedules, and `next_run` is booked before running so failures can't loop.

Model freedom: an OpenAI-compatible layer with explicit `providerId/model` refs, presets for Ollama/OpenAI/Anthropic/OpenRouter/Gemini, real per-call cost tracking, and it works fully offline with local Ollama. On a Mac, native Ollama is supported so the GPU is actually used.

Everything heavier (SearXNG, a browser agent, Ollama-in-Docker, Open WebUI, …) is an optional add-on, off by default — you pay for nothing you don't use.

Install is one command:

```bash
curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install-server.sh | bash
```

There's also a company of agents with real controls: per-agent monthly budgets in cents, checked before every model call, delegation depth capped at 3, and a Kanban board. A mission-control dashboard is on the roadmap (Phase 3).

Repo: https://github.com/Inclined88888/ai-workspace — the README carries an honest tested/untested table, and the measured numbers are published and reproducible.

## 2. Blog / Reddit launch post

**AI Workspace: your own AI workspace, in one light container**

Most self-hosted AI stacks ask you to wire together a chat UI, an agent framework, a vector database, and a scheduler before you get any value. AI Workspace ships all of it as one ~350 MB container that idles around ~100 MB of RAM. A €4 VPS or a Mac laptop is enough.

**A workspace, not just a chatbot.** Workbench — the core app — includes notes with AI writing assist, tasks, calendar (ICS read, CalDAV read+create), mail (IMAP/SMTP), research with citations, side-by-side model comparison, and searchable history across every conversation. The assistant doesn't just talk about these — it acts on them through tools, with each tool step visible live in the chat.

**Memory that earns its keep.** After each turn, the assistant quietly extracts durable facts about you — capped at 5 per turn, with a 40-character minimum and deduplication, so memory doesn't bloat. Recall uses FTS5 bm25, so there's no vector database to run. Relevant memories are injected into every new conversation, and you can turn the whole thing off.

**Safety by construction.** Mail is draft-only — it never sends. There's no shell tool by default. Secrets are encrypted at rest, the CSRF header is required on every mutating API call, tool use is capped at 12 steps per turn with a 180-second timeout, and scheduled runs can't create schedules or loop on failure.

**A company of agents with real controls.** Give your agents a mission and an org chart with `reports_to` (loop-checked, delegation capped at 3 levels). Each agent has a monthly budget in cents and a token limit, checked before every model call — overshoot is bounded to a single call, and over-budget agents auto-pause. One worker runs the queue, so there's no runaway parallelism. A mission-control dashboard and one-click company templates are on the roadmap (Phase 3).

**Privacy and local-first.** Everything stays inside your container: SQLite, no telemetry, no account, no vendor lock-in. Model API keys are stored encrypted, locally, and the whole thing works fully offline with local Ollama.

**Model freedom.** An OpenAI-compatible layer means most providers just work, with explicit `providerId/model` references so you always know which model ran. Presets cover Ollama, OpenAI, Anthropic, OpenRouter, Gemini, OpenCode Go, and OpenCode Zen; free models are marked "(free)" in menus, and OpenRouter's real per-call cost is tracked.

**Add-ons without the bloat.** Web search, a browser agent, local models in Docker, Open WebUI, and upstream agent systems are all optional Compose profiles — off by default, adding weight only when you ask for it. An add-on manifest spec and a community registry (never auto-installed) are on the roadmap (Phase 5).

**Install in one line.** `curl | bash`, with OS and Docker checks that prompt instead of failing silently, bash 3.2 compatibility for stock macOS, and a printed URL plus one-time setup code at the end. Upgrades keep your `.env` and data, and re-running the installer is idempotent.

More channels are coming as scoped, allowlisted add-ons — Signal, WhatsApp, Discord, Slack (Phase 4). Telegram already ships in the core.

Try it: https://github.com/Inclined88888/ai-workspace
