# Model integrations: what each one is, and what was proven

ROOKS reaches models in five different ways. They are not interchangeable, and the connection directory (Settings,
AI models, All model connections; `GET /api/connection-directory`, `app/connections.py`) shows each in its own group.

| | Category | Examples | Powers ROOKS chat | Billing | Who installs and starts it |
|---|---|---|---|---|---|
| A | API inference providers | OpenAI, Anthropic, OpenRouter, Google Gemini, OpenCode | Yes | Per token to your key, or a provider's free tier | Nothing to install: you add a key |
| B | Coding agents with their own sign-in | OpenAI Codex CLI, Claude Code | **No** | The agent's own sign-in (see below) | You, on the computer that runs the ROOKS Companion |
| C | Local runtimes | Ollama, llama.cpp, LM Studio, the AirLLM bridge (experimental) | Yes (AirLLM: plain answers only) | Nothing billed, unless a paid API is behind the server | You. ROOKS never installs one, downloads a model or starts a process by itself |
| D | Provider libraries inside ROOKS | any-llm (Mozilla AI) | Not a connection | The connection's | Optional `pip install` |
| E | Other OpenAI-compatible servers | a gateway, vLLM, a hosted endpoint | Yes | Unknown to ROOKS: treated as possibly paid | You |

## Capability tests

A key that is accepted proves that the key is accepted. ROOKS records each of these separately, per model, with the
time and what happened (`app/verify.py`, stored as `cap_check:<model>`), and shows them in the directory:

| Test | Passes when |
|---|---|
| chat | the model answers "Reply with only the word READY" with READY |
| tools | it calls `lookup_code` with `room: B12` |
| tool_roundtrip | given the tool's result (`4417`), it uses it in its answer |
| streaming | the answer arrives in two or more chunks (one chunk means the server accepted `stream` but did not stream) |
| json | it returns `{"sum": 42, "word": "blue"}` for a simple instruction |
| vision | it names the colour of the left half of a test image |
| embeddings | the embeddings endpoint returns a vector |

Tests run only when you ask, with output capped as in connection checks, through the same guard as every model call
(Free Cloud AI and Local AI refuse models they do not allow; the monthly spending limit applies). A test that could be
charged asks first. A passed tools test counts as tested tool use for model roles.

## Model selection

One picker everywhere (`util.tsx`, `ModelSelect`). **Automatic** is the default: the model ROOKS last saw answer a
real request with a tool attached, among those the AI mode allows. The list is grouped:

- **Ready**: a real request with a tool attached succeeded.
- **Not tested yet (may not work)**: listed by the provider, never tried. Selectable, but in its own group.
- **Not available in this AI mode**: listed, disabled, with the reason. Free Cloud AI never substitutes a paid model and
  Local AI never a cloud model, backups included.

Per-role models (chat, planning, research, coding, documents, vision, long context, local) are under Model roles; a
role is refused for a model whose required capability was not tested.

## A. API providers

Unchanged from v11.1 (see `docs/AI_MODES.md`). Anthropic connections still use Anthropic's OpenAI compatibility
endpoint unless you switch them to the any-llm engine (D).

## B. Coding agents, through the ROOKS Companion

ROOKS's server runs in Docker; coding agents run on your computer, next to your code. The companion
(`companion/rooks_companion/agents.py`) runs the agent's official command line tool for one approved task, in one folder
you allowed with `rooks-companion allow-folder`. ROOKS never reads, copies or sends an agent's sign-in files or
tokens, never uses a subscription for anything but that agent's own task, and never turns off an agent's sandbox or
permission checks.

The flow, in the conversation: `coding_agents` (what the paired computer has) → `run_coding_agent` → an approval that
names the folder and the billing (always, whatever the permission settings) → the companion runs it → progress on a
card with Stop → the agent's final message posted in the conversation.

### OpenAI Codex CLI

- Command: `codex exec --json --sandbox workspace-write --skip-git-repo-check -C <folder> <task>`, stdin closed,
  a minimal environment (no other API keys pass through).
- Sign-in: ROOKS runs `codex login status` and keeps only which kind it is. Signed in with ChatGPT, Codex uses your
  plan's Codex usage limits; signed in with an API key, OpenAI bills that key. Neither powers ROOKS chat, and ROOKS
  does not treat a ChatGPT plan as an API key.
- Sandbox self-test: before the first task the companion asks Codex's own sandbox to write outside a test folder and
  checks the result. If the write gets through, or the result is unclear, ROOKS refuses to run Codex on that computer.

Evidence (Codex CLI 0.165.0, npm `@openai/codex`, Apache-2.0, on the Linux test container, as root):

| What | Result |
|---|---|
| `codex --version` | `codex-cli 0.165.0` |
| `codex login status`, not signed in | `Not logged in`, exit 1 |
| after `codex login --with-api-key` (a fake key, for a stand-in server) | `API key configured - sk-stand***l-key`, exit 0. ROOKS classifies it as an API key and keeps no text |
| `codex exec --json` against a local stand-in of the Responses API (`companion/tests/standins/responses_api.py`) | Real event stream: `thread.started`, `turn.started`, `item.started` and `item.completed` (`command_execution`, `agent_message`), `turn.completed`; recorded in `companion/tests/fixtures/codex_exec.jsonl` |
| a command writing outside the folder, through `codex exec` and `codex sandbox` | `Read-only file system`; the folder itself was writable |
| writing under `/tmp` | allowed: Codex's workspace-write mode allows the temp folders by design |
| ChatGPT sign-in | **Not tested**: no ChatGPT account was used. The `chatgpt` classification matches on the word ChatGPT in the status text |
| macOS (Seatbelt) or Windows sandbox | **Not tested** |

### Claude Code

- Command: `claude --bare -p <task> --output-format stream-json --verbose --permission-mode acceptEdits
  --allowedTools=Read,Edit,Write,Glob,Grep`. No shell tool.
- Claude Code's own help says of `--bare`: "Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper via --settings
  (OAuth and keychain are never read)". So a Claude subscription sign-in on that computer is never used.
- It runs only after you turn it on for the companion (`rooks-companion allow-claude-api on`) and set
  `ANTHROPIC_API_KEY` for it. Anthropic bills that key per use; Claude Code reports the cost, and ROOKS shows it.
- **ROOKS chat cannot use a Claude subscription.** ROOKS chat can use an Anthropic API key (category A). The UI says so
  in the directory and in the approval.

Evidence (Claude Code 2.1.312, npm `@anthropic-ai/claude-code`, licence: see its README, run only with `--bare` and an
emptied environment on the test container):

| What | Result |
|---|---|
| `claude --version` | `2.1.312 (Claude Code)` |
| a task against a local stand-in of the Messages API (`companion/tests/standins/messages_api.py`) with a fake key | Init event reports `apiKeySource: ANTHROPIC_API_KEY`; the stand-in received `x-api-key` and no `Authorization` header; the tool call ran and the file was written; recorded in `companion/tests/fixtures/claude_stream.jsonl` |
| a real Anthropic API key, a real Claude subscription | **Not tested** |

## C. Local runtimes

ROOKS never installs Ollama or llama.cpp, never downloads a model without your agreement to its size and licence, and
never starts an inference server. Connecting one sends one connection-check request, as for any provider.

- **Ollama, llama.cpp**: presets unchanged since v11.1. Not installed on the test machine; **not tested** in v13.
- **AirLLM** (`addons/airllm/`, experimental, off by default): AirLLM 4.0.0 (Apache-2.0) has no server, so a separate
  bridge script serves one model on an OpenAI-compatible address. Tested for real on CPU with
  `hf-internal-testing/tiny-random-LlamaForCausalLM`: import 28.4 s, load 7.6 s, 5 tokens in 8.3 s (5.6 s through the
  bridge); the bridge answers `/v1/models`, chat completions and a streaming request (as one chunk). ROOKS's capability
  tests then **fail** chat, tools and streaming for it, which is the point: the package installs and the bridge
  answers, but nothing shows it works as a model. A real model, a GPU and Apple Silicon were **not tested**.

## D. any-llm (Mozilla AI)

Evaluated: any-llm-sdk 1.40.2, Apache-2.0. Its base install requires `openai` (2.18 or later, below 4), `anthropic`
(0.124 or later, below 1), `pydantic` 2, `openresponses-types`, `rich`, `httpx`: about 91 MB of packages in a fresh
virtual environment.

Decision: **adopt narrowly**. Every provider ROOKS supports except Anthropic already speaks the OpenAI chat format
natively, so any-llm would add a layer and 91 MB without improving those. Anthropic is reached through a compatibility
endpoint, so there any-llm's native Messages API path is a real improvement. It is therefore:

- optional (not in `requirements.txt`; `pip install any-llm-sdk==1.40.2`), imported only when used;
- per Anthropic connection, off until you switch it in the directory; existing connections keep the compatibility
  endpoint, and their capability results are cleared when you switch so they are tested again;
- behind the same guards (`llm._call` and `llm.stream` route to `app/anyllm_engine.py`).

Evidence (`workbench/backend/tests/test_v13_connections.py`, against a stand-in of the Messages API): chat, tool call
(tools sent as Anthropic `input_schema`), tool round trip and streaming pass; every request went to `/v1/messages` with
`x-api-key`, never to `/chat/completions`; the 64 token output cap of capability tests reached the native request; an
unreachable server is explained like other provider errors. any-llm warns that its provider-specific exceptions will
become unified exceptions in a future version, so `anyllm_engine.error_text` reads only `status_code` and the message.
Embeddings, images and structured output through any-llm: **not tested**. Against the real Anthropic API: **not tested**.

## E. Other compatible servers

Anything that is not a known provider host and not on your network. ROOKS cannot see their prices, so billing shows as
unknown and capability tests ask before sending.
