# Audit and direction (2026-10-28)

The brief calls the product Rook. Nothing in this repository is named Rook: it is "AI Workspace", whose core is **Workbench**. This file records what was verified, what was changed, and what should happen next. Evidence labels:

* **VERIFIED**: shown from the source code or by a test that was run.
* **BROKEN**: failure reproduced, with the procedure given.
* **INFERRED**: strongly supported by code, not demonstrated.
* **UNVERIFIED**: needs hardware, credentials or access that was not available.

## 1. Verdict

It is **a small, reasonably built core (Workbench) inside a bundle of six optional upstream products whose responsibilities overlap with the core and with each other.** It is not a coherent product yet.

The strongest evidence that it is a bundle and not one system:

| Responsibility | Implementations in this repo |
| --- | --- |
| Chat surface | Workbench Assistant, Open WebUI add-on, Hermes add-on (TUI + dashboard) |
| Memory, skills, schedules | Workbench (`learn.py`, `scheduler.py`), Hermes (own volume) |
| Multi agent "company" | Workbench Company (`company.py`), Paperclip add-on |
| Tool registry for the same operations | `assistant.py` TOOLS, `tools.py` (OpenAPI), `mcp_server.py` (MCP), each with a different tool set (VERIFIED: before this change the built-in assistant could not read an email body or list events, while the MCP and OpenAPI servers could) |

* State is split across five volumes with separate logins. The core is one container; add-ons add 2.5 to 13 GB each (README figures, not re-measured).
* **Most serious architectural mistake:** the product is a Docker server stack. That fits "always-on assistant on a VPS". It structurally cannot deliver the stated vision of local computer control, the user's own files, a desktop install for non-developers, or app control. Everything the agent touches lives in a container volume.
* **Most serious usability problem:** a normal user needs Docker, a terminal, `setup.sh`, and either an API key or a separately installed Ollama. They then land in a 14-item sidebar plus links to up to three other apps with their own logins. The core install has no working web search at all (`search.py`: no SearXNG add-on and no Brave or Tavily key means `web_search` raises "Web search is not set up").
* **Most important missed opportunity:** across OpenClaw, Hermes Agent and Claude Code, the same three problems recur in public issue trackers:
  * local model tool calling that silently fails,
  * prompt injection with no execution boundary,
  * context loss and broken recovery after interruption.

  Workbench's core is small enough to solve these properly. Before this change it had all three (section 2).

## 2. Verified findings in the core path

Request path: browser `Assistant.tsx` → `POST /api/assistant/chat` (`assistant_api.py`) → `assistant.run_turn` → `llm.chat` (OpenAI-compatible POST) → `assistant.run_tool` → SQLite → SSE events back. One loop serves web, CLI (`cli/ws`), Telegram, schedules and company agents (VERIFIED, `assistant.py` docstring and callers). That shared loop is the best design decision in the repo; keep it.

| # | Finding | Status | Evidence |
| --- | --- | --- | --- |
| F1 | Ollama through `/v1` runs at a 4096-token window regardless of the model, and silently cuts the start of the prompt (system prompt, memories, tool rules) | **BROKEN** (fixed) | Ollama 0.40.12, qwen3:1.7b: a 7,147-token prompt was logged as `truncating input prompt limit=2050 prompt=7147`. The model answered "logistics" instead of the code word in the system prompt, and the HTTP status was 200. The same prompt on native `/api/chat` with `num_ctx: 16384` answered correctly. A bare first agent turn already used 1,370 tokens. Research sends up to 8 × 6,000 characters, so every local research report was built on a cut prompt. |
| F2 | A turn interrupted between a tool call and its result leaves an unanswered call. Providers that enforce pairing (OpenAI, Anthropic) then reject every later request in that conversation | **BROKEN** (fixed) | `tests/test_recovery.py`: the stream is closed at the `tool_call` event, which is what Starlette does on client disconnect. The next message returned `400 tool_call_ids did not have response messages`. The Stop button (`AbortSignal` in `api.ts`) and closed tabs trigger this. |
| F3 | No execution boundary. Every tool ran on the model's say-so, including ones that persist behavior (remember, save_skill, schedule_task) or act outside (CalDAV create_event, browse_web, delegate_task), right after reading attacker-controlled web or email content | VERIFIED (fixed) | `assistant.run_tool` had no policy. `learn.mine_skill` fed raw `TOOL RESULT` text into skill extraction, and saved skills are injected into every later system prompt, so one poisoned page could persist instructions indefinitely. |
| F4 | Models that print a tool call as text get no tool run; the text is shown as the answer | VERIFIED (fixed) | `llm.chat` only read structured `tool_calls`. This is the single most reported local-model failure in OpenClaw and Hermes (section 3). |
| F5 | History is a fixed 40 messages with tool results up to 20,000 characters each, and no size budget | VERIFIED (fixed: budget; not fixed: summarizing compaction) | `assistant._history` |
| F6 | `scripts/hermes-config.sh` pointed Hermes at the `ollama` container even when setup chose native Ollama on the host (`OLLAMA_URL=http://host.docker.internal:11434`, which is the README's recommended Mac path) | VERIFIED (fixed) | `setup.sh` line 125 vs `hermes-config.sh` line 25 |
| F7 | No web search in the core install | VERIFIED (not fixed) | `search.provider()` returns `""` without SearXNG or a key |
| F8 | The MCP and OpenAPI tool servers expose write tools (create_event, create_note, save_email_draft) to Hermes, Open WebUI and Paperclip agents with a bearer key and no approval or taint tracking | VERIFIED (not fixed) | `mcp_server.py`, `tools.py`. The new boundary covers Workbench's own assistant only. |
| F9 | `research.fetch_text` checks DNS then lets httpx resolve again (TOCTOU / DNS rebinding) | INFERRED | `research._public_host` |
| F10 | No shell, no file tools, no computer or app control in the core | VERIFIED | none exist in `assistant.TOOLS` |
| F11 | Learning runs one extra model call per turn and a skill call after 3+ tools, on the conversation's model | VERIFIED | `learn.after_turn` |

Unverified: the desktop and Docker install on a clean machine, Telegram against the real API, CalDAV and IMAP against real servers, the browser-agent service (needs browser-use and Chromium; its tests were not run here), and any cloud provider (no keys in this environment).

## 3. Competitive position (researched 2026-10-28; sources linked)

| Capability | AI Workspace after this change | Rating | Reference |
| --- | --- | --- | --- |
| Shell, files, coding | none | **BEHIND** all | Claude Code, Codex (sandboxed shell, approval modes), OpenClaw, Hermes |
| Computer or app control | none | **BEHIND** | Claude Desktop (computer use), OpenClaw nodes |
| Messaging channels | Telegram | **BEHIND** | OpenClaw "20+", Hermes six platforms ([OpenClaw](https://github.com/openclaw/openclaw), [Hermes](https://github.com/nousresearch/hermes-agent)) |
| Local model tool calling | native Ollama API with explicit window, plus validated text-call recovery | **PARITY or better, small sample** | OpenClaw also requires native `/api/chat` ([docs](https://docs.openclaw.ai/providers/ollama)) and has no parser fallback ([PR #171898](https://github.com/openclaw/openclaw/pull/171898)). for Hermes, 10 issues were found where tool calls land in content or are never sent (open/closed state not checked) ([#5867](https://github.com/NousResearch/hermes-agent/issues/5867), [#66452](https://github.com/NousResearch/hermes-agent/issues/66452)). Only qwen3 4B and 1.7B were tested here. |
| Approval scope after untrusted input | covers browse, memory, skills, schedules, external writes | **AHEAD of OpenClaw** on browser scope, **BEHIND** Claude Code and Codex on OS sandboxing | OpenClaw approvals do not govern browser automation ([docs](https://docs.openclaw.ai/tools/exec-approvals)). Codex uses Seatbelt/bwrap sandboxes ([docs](https://learn.chatgpt.com/docs/agent-approvals-security)). |
| Recovery from interrupted turns | repaired automatically | **PARITY** (narrow: one turn) | Claude Code has 7+ `--resume` crash issues ([#18880](https://github.com/anthropics/claude-code/issues/18880)) |
| Long-context handling | budgeted trimming, no summarizing compaction | **BEHIND** on long tasks | Claude Code, OpenClaw and Hermes all compact, and all have issues losing work to it |
| Network exposure default | binds 127.0.0.1 | **AHEAD** of OpenClaw's history | CVE-2026-25253 and mass-exposed gateways ([clawdocs](https://clawdocs.org/security/known-vulnerabilities)) |
| Install for non-developers | Docker + terminal | **BEHIND** desktop apps | Claude Desktop, OpenWork (Electron) |
| Memory quality | FTS5 facts plus profile | **UNKNOWN** | no comparable benchmark run |

Constraints that shape strategy (sourced):
* **Claude subscriptions cannot legally power a third-party harness.** Anthropic's terms forbid third-party apps from routing Free, Pro or Max credentials; only the unmodified Claude Code binary with the user's own login is allowed ([legal page](https://code.claude.com/docs/en/legal-and-compliance)). An integration must use API keys, or hand off to the user's own Claude Code install.
* **ChatGPT plans** can be used through OpenAI's official "Sign in with ChatGPT", which is open to registered open-source clients ([docs](https://learn.chatgpt.com/docs/sign-in-with-chatgpt), [quickstart](https://developers.openai.com/siwc/quickstart)). This is a legitimate path worth implementing.
* **AirLLM** is a Python API only: no HTTP server, streaming, chat templates or tool calling, and it decodes very slowly by design ([repo](https://github.com/lyogavin/airllm)). It is not an agent backend without a custom server, and should not be prioritized.
* **llama.cpp** needs `--jinja` and a tool-capable template, and KV-cache quantization degrades tool calls. **LM Studio** returns malformed calls as plain content. **vLLM** needs an explicit `--tool-call-parser` ([llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md), [LM Studio](https://lmstudio.ai/docs/developer/openai-compat/tools), [vLLM](https://docs.vllm.ai/en/latest/features/tool_calling.html)). The text-call recovery added here is useful across all three.

Recurring market complaints, ranked by breadth of independent evidence found: context loss and compaction; security (malicious skills, exposed gateways, prompt injection); weak local tool calling; cost and subscription limits; install and update breakage; browser automation flakiness; poor resume; approval fatigue; opaque actions. Full research notes, with links, came from two research passes and are summarized here, not reproduced.

## 4. Product definition

**What it should be:** a private assistant that runs on hardware you own, works with a local model and no subscription, and is safe to leave connected to your mail and calendar. Every action it takes is visible, and every action that matters is yours to approve.

**First users:** people who already run a home server, NAS or always-on Mac, want an assistant over their own mail, calendar, notes and tasks, and do not want a cloud vendor holding that data. They are not developers looking for a coding agent; those users are better served by Claude Code, Codex or OpenCode.

**Main jobs:** triage and draft email, plan the day and week from calendar and tasks, research a question with real citations, keep notes, run scheduled briefings. All of it from web, terminal and Telegram over one memory.

**Why choose it:** it is local-model-first and safe by construction. It is not a feature count. The defensible claim, which must keep being proven with tests like the ones added here, is: "works with a 4B local model, cannot be hijacked into lasting changes by a web page or email, and never loses a conversation to an interruption."

**Defer:** coding agents, the Company feature, desktop computer control, the multi-agent org chart, and model comparison.

## 5. Target architecture

Keep a **modular monolith**: one Python process, one SQLite database. Workbench already is this, and nothing measured here justifies more processes. The browser stays a separate process because Chromium needs isolation.

```
Clients (web UI, cli/ws, Telegram, MCP for outside agents)
        │  one HTTP API, one auth
Agent runtime (assistant.run_turn)  ── policy: effect classes, taint, approvals (pending_actions)
        │                                   │
Model layer (llm.py)                 Tool registry (ONE list; OpenAPI and MCP generated from it)
  native adapters: Ollama /api/chat,        │ read │ write │ external │ persistent, untrusted flag
  OpenAI-compatible, Anthropic native       │
  capability record per model         Integrations: notes, tasks, calendar, mail, search, browser service
        │
Storage: SQLite (conversations, memories, skills, schedules, actions, usage)
Workers: scheduler loop, calendar sync, learning (same process)
```

Local request to completed tool: user text → `run_turn` (taint from history) → `llm.chat` (native Ollama with `num_ctx`, structured calls, or validated text calls) → `run_tool` (policy check: run, hold for approval, or refuse) → result labelled if untrusted → saved to history → next model step → final message plus events.

| Component | Decision | Why |
| --- | --- | --- |
| Workbench core (FastAPI + SQLite + React) | **KEEP** | small, tested, single loop across channels |
| `assistant.TOOLS`, `tools.py`, `mcp_server.py` | **MERGE** | three registries already drifted apart. Generate OpenAPI and MCP from TOOLS so policy applies to outside agents (F8). |
| `llm.py` | **REFACTOR** | native Ollama done. Next: native Anthropic (the OpenAI shim is described by Anthropic as not for production), capability probing (tools, vision, context) stored per model |
| Company (`company.py`, page) | **REMOVE** | duplicates Paperclip, multiplies inference cost, no evidence it completes work better than one agent |
| Compare page | **REMOVE** from navigation (keep the endpoint for benchmarking) | developer tool, not a user job |
| Open WebUI add-on | **REMOVE** | second chat app with separate users, memory and tools |
| Hermes add-on and `assistant/` | **REMOVE** from this product (it is a good standalone product) | a second assistant with its own memory, skills and schedules. Two sources of truth about the user. |
| Paperclip add-on | **REMOVE** from this product | coding-agent company; wrong audience; 13 GB |
| Browser agent service | **KEEP, REFACTOR** | needed for real browsing. Next: an accessibility-tree page-reading tool (Playwright) instead of handing the whole goal to a second agent loop |
| SearXNG add-on | **KEEP, make default** | the only keyless search; core has none (F7) |

## 6. Interface

* **Primary surface:** the conversation. Notes, Tasks, Calendar and Mail stay as views, because people do need to look at their own data, but they live under one "Library" entry. Memory, skills and automations move into a single "What it knows and does" view. Settings collapses providers, search and channels into one "Connections" page.
* **Navigation target:** 5 items (Chat, Library, Activity, Connections, Settings), down from 14 plus external links.
* **Activity view:** one timeline of tool calls, approvals, schedule runs and failures. The data now exists in `pending_actions` and `messages`.
* **Onboarding:** detect Ollama on the host and offer one model sized to detected RAM (the API already lists models), turn on keyless search, then show one guided first task. Target: from first launch to a completed useful task in under 10 minutes on a clean Mac, measured with a stopwatch on a fresh machine (not done here).

## 7. Roadmap

Scoring: impact on the core promise (0 to 3) + severity (0 to 3) + workflows affected (0 to 3), minus effort (0 to 3). Higher is first. Dependencies decide order inside a phase.

**P0, foundational (critical path)**
1. **One tool registry** (score 7). Generate OpenAPI and MCP from `assistant.TOOLS`, apply the same policy, and add taint for outside agents per request. Acceptance: one list. An MCP client calling `create_event` after a tool that returned web content gets a held action. Tests: a parity test over the three surfaces.
2. **Keyless web search in core** (score 7). Make SearXNG the default add-on, or ship a bundled minimal search. Acceptance: a fresh `./setup.sh --yes` completes Scenario B. Test: setup smoke test in CI with Docker.
3. **Model capability record** (score 6). Probe tools, context and vision at provider add; refuse to run tools on models that fail a 3-call probe, with an explicit message. Acceptance: the model picker shows verified capabilities.
4. **Native Anthropic adapter** (score 5). Removes the dependency on a shim Anthropic itself says is not for production.

**P1, coherence**
5. Remove Company, Compare navigation, and the Open WebUI, Hermes and Paperclip add-ons, with an export path for existing data (Paperclip and Hermes data stay in their volumes; document how to keep running them standalone).
6. Activity view plus a 5-item navigation.
7. Summarizing compaction with pinned task state (goal, open items, approvals), not just trimming. Acceptance: a 60-step research task keeps its goal.
8. Resumable turns: persist an "in progress" marker and offer "continue" after a restart.

**P2, capabilities**
9. Sandboxed workspace files and shell, but only in a desktop or host-agent mode (see 10), behind the same approval classes, with an OS sandbox (bwrap or Seatbelt, as Codex does).
10. A host agent (small signed binary) that gives the server access to the user's own files and apps, opt-in per folder. This is the real answer to F10 and to the "local computer" vision. A container cannot do it.
11. Sign in with ChatGPT (official open-source client registration).
12. Playwright accessibility-tree tools for browsing.

**P3, differentiation:** published reliability benchmarks per local model (the live harness in section 9 is the start), a desktop package, more channels.

**Must work before any new feature:** items 1 to 3, plus Scenarios A, B, F and G passing on a clean install.

## 8. What changed in this pass

Commit `Harden the agent loop: native Ollama, approvals, interrupted turns`, plus the tool-parity follow-up:

* `llm.py`
  * The Ollama path now uses native `/api/chat` with `options.num_ctx` (`OLLAMA_NUM_CTX`, default 16384) for chat, complete and streaming.
  * A prompt that fills the window raises an error.
  * `context_chars()` gives the per-model budget.
  * `parse_text_tool_calls()` recovers `<tool_call>` or bare JSON calls, only for offered tools with valid object arguments. Recovered calls are flagged `parsed_from_text`.
* `assistant.py`
  * Tools carry `effect` and `untrusted`.
  * `needs_approval()` holds external and persistent actions once untrusted content is in the context window.
  * Unattended channels refuse instead of holding.
  * `decide()` runs an approved action once (atomic claim).
  * `_history()` repairs unanswered calls and fits a character budget, always cutting on a user message.
  * `read_email` and `list_events` were added to the assistant.
* `learn.py`: no skill is learned from a turn that read untrusted content.
* `research.py`: sources are sized to the model's window.
* `db.py`: `pending_actions` table.
* `assistant_api.py`: `GET /api/approvals`, `POST /api/approvals/{id}`.
* `Assistant.tsx`: approval cards. `cli/ws`: `approvals`, `approve`, `deny`. `telegram.py`: tells the user what is waiting.
* `hermes-config.sh`: uses `OLLAMA_URL`. `docker-compose.yml` and `.env.example`: `OLLAMA_NUM_CTX`. `README.md`: documented.

Deliberately unchanged:
* **No features were removed.** Deleting Company or the add-ons changes what existing users have. It is listed as P1 with a migration, for the owner to approve.
* **No shell or file tool was added.** In a server container it would touch only a volume the user cannot see, while adding the largest attack surface. It needs the host-agent design first.
* **No search scraping** was added, given scraping's terms-of-service and reliability problems.

## 9. Validation

Environment: Linux container, 4 vCPU, 15 GB RAM, no GPU. Ollama 0.40.12 (CPU), models qwen3:4b (the project default) and qwen3:1.7b. No cloud keys, no Docker daemon test, no browser service.

Automated tests (`cd workbench/backend && pytest`): **24 passed** (14 before). The fake model now enforces OpenAI's tool-pairing rule, and a fake native Ollama endpoint was added.

| Scenario | Result | Evidence |
| --- | --- | --- |
| A. Local assistant, real tool, no cloud | **PASS** (qwen3:4b, on both the old `/v1` path and the new native path) | Real Ollama, real agent loop: "Add a task to renew my passport, due 2026-12-01, high priority" led to a native `create_task` call and the row was written to SQLite. 338 s, then 344 s on the native path. Broader measurement: the 50-task benchmark below. |
| B. Live research with citations | **BLOCKED** in core | No search provider without the SearXNG add-on or a key (F7). Reading a real public page with a local model is part of the in-progress run. |
| C. File operations | **NOT TESTED** | No file tools exist (F10). Notes and documents are database rows. |
| D. Model switching | **PASS** (automated, fake providers) | `test_ollama_uses_native_api_with_a_real_context_window`: the same server switches between an OpenAI-shaped and an Ollama-native provider per conversation, with tools working on both |
| E. Integrated workflow | **PASS** (automated, scripted model) | `test_skill_created_after_complex_task`: agenda, tasks and notes in one request |
| F. Failure recovery | **PASS** (automated) | `test_interrupted_turn_does_not_break_the_conversation` (failed before the fix), `test_tool_errors_go_back_to_the_model`, full-window error test |
| G. Prompt injection boundary | **PASS** (automated, scripted model that obeys the injection); **inconclusive** on a real model | The four tests named in the original row. The real qwen3:4b read the poisoned page, then the next model call hit the old 300 s timeout, so it never decided. The empty memory table proves nothing. |
| H. Fresh install | **NOT TESTED** | No clean machine or Docker build was exercised here |
| I. Continuity across web, CLI, Telegram | **PARTIAL** | All three call the same `run_turn` and database (VERIFIED in code). The approval flow is wired into all three; only the HTTP API path is under test. |

Live finding: the background learning call and the next turn competed for Ollama's single slot, both hit the 300 s read timeout, and the user saw a bare `ReadTimeout`. Fixed in the receipts commit: local timeout 900 s (`OLLAMA_TIMEOUT_S`), and timeouts are clear, recorded errors.

Other checks:
* Frontend `tsc --noEmit` and `vite build`: clean.
* `browser-agent` tests: 24 passed with `CHROME_PATH` pointing at an installed Chromium. Without it, 6 failed, because `pyproject.toml` leaves Playwright unpinned (`>=1.50`). Playwright 1.64.1 then expected a browser build that was not installed. This will hit users too; pin it.
* Measured cost of local inference here: qwen3:4b on 4 CPU cores generates about 4.8 tokens/s with thinking on. A single tool turn took minutes. That is usable for scheduled jobs and unusable for interactive chat without a GPU or Apple silicon.

## 9b. Receipts and the reliability record (phase 1 of "earned trust")

* `receipts.py` checks every change in the database after the tool runs. A tool that returns without making the change fails with "the change could not be confirmed".
* Final answers that claim a change with no confirmed change behind them are withdrawn and retried once. If the claim is still unbacked, it is shown marked **Not verified**.
* Every tool call and turn is recorded per model in `tool_runs` and `turn_checks`. They are exposed by `GET /api/reliability`, `ws reliability`, and the model picker.
* Tests: `tests/test_receipts.py`, 8 tests, including a precision test on the claim detector (8 true claims, 8 non-claims).
* Limits:
  * The claim detector is pattern based. A claim worded outside its patterns passes unchecked; the benchmark's loose matcher exists to find those cases.
  * `browse_web` results cannot be verified and say so.
  * Phase 2 (changing behavior per model based on the record) is not built.
* Benchmark: `bench/local_tasks.py`, 50 real requests checked against the database. Results are in the next section.

BENCH_RESULTS

## 10. Continuation point

Next step: P0 item 1 (one registry). Start from `assistant.TOOLS`, which now holds effect classes and is the most complete list. Generate the `tools.py` routes and `mcp_server.py` tools from it. Add a per-request taint flag for outside agents. Then delete the hand-written duplicates.
