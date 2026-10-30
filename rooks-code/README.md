# Rooks Code: the terminal coding agent

`rooks` is AI Workspace's own coding agent for the terminal, in the spirit of Claude Code and Codex CLI, but it works
with **any model**: OpenAI, Anthropic, OpenRouter, any OpenAI-compatible server (Groq, Together, vLLM, llama.cpp, ...)
and local **Ollama** or **LM Studio**, fully offline. It does not need Workbench or Docker.

It reads and searches your code, edits files (showing a diff first), runs commands (tests, builds, git), and loops until
the job is done. Everything it does is asked about first, can be undone, stays inside a hard budget, and is written to a
receipt in Workbench's run-log format.

## Install

Rooks is pure Python standard library (no dependencies), Python 3.11 or newer.

| | Recommended | Alternatives |
|---|---|---|
| macOS | `brew install uv` then `uv tool install "git+https://github.com/Inclined88888/ai-workspace#subdirectory=rooks-code"` | `pipx install ...` (same URL) |
| Windows | `winget install astral-sh.uv` then the same `uv tool install` line (PowerShell) | `py -m pip install --user ...` |
| Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` then the same `uv tool install` line | `pipx install ...` |

From a clone: `uv tool install ./rooks-code` (or `pip install ./rooks-code`). Single file: `python rooks-code/scripts/build_pyz.py`
makes `dist/rooks.pyz`, which runs anywhere with `python rooks.pyz`.

**Why this packaging.** The rest of this repo's terminal tooling (`cli/ws`) is dependency-free Python, so the agent is
too: nothing to audit, no supply chain, nothing that phones home. `uv tool install` is a single static binary on all three
systems, installs a private Python 3.11+ automatically when the machine has none (stock macOS ships 3.9), puts `rooks`
on the PATH and upgrades/uninstalls cleanly (`uv tool upgrade rooks-code`, `uv tool uninstall rooks-code`). We chose this
over a Node/npm package (would add a runtime the core does not use) and over PyInstaller binaries (unsigned executables
trigger Gatekeeper/SmartScreen warnings until code-signing exists; the desktop app track handles signing). The `.pyz`
is the no-installer fallback. CI tests and installs it on macOS, Windows and Linux.

## Use

```sh
cd your-project
rooks                                  # interactive session
rooks "make the failing test pass"     # one prompt, prints the answer, exits 1 if it did not finish
rooks -P ollama -m qwen2.5-coder:7b    # local and offline (ollama pull qwen2.5-coder:7b first)
rooks -P anthropic -m claude-sonnet-4-5   # needs ANTHROPIC_API_KEY
rooks -P openai -m gpt-4.1-mini        # needs OPENAI_API_KEY
rooks -P openrouter -m deepseek/deepseek-chat   # needs OPENROUTER_API_KEY
rooks -P openai-compatible --base-url http://localhost:8000/v1 -m my-model
rooks --dry-run "rename foo to bar everywhere"  # show every diff and command, change nothing
```

Inside a session: `/undo`, `/cost`, `/dry-run`, `/yes`, `/model NAME`, `/receipt`, `/clear`, `/exit`.

Other commands: `rooks undo`, `rooks receipts [list|show|import --db workbench.db]`, `rooks doctor` (checks the provider,
key and MCP servers with one real call), `rooks mcp` (lists MCP tools), `rooks init` (writes an example config).

## Trust controls

* **Approval by default.** Reading and searching run freely. Every edit is shown as a diff and every command in full,
  then you answer `y`, `n` or `a` (always, for this session). A denied action is reported to the model, which is told not
  to retry it. With no terminal input the answer is "no".
* **Trusted folders.** `trusted_folders = ["~/code/my-app"]` in the config lets edits and commands in those folders (and
  below) run without asking. `--yes` does the same for one run.
* **Dry run.** `--dry-run` (or `/dry-run`) shows everything that would change and applies nothing.
* **Undo.** Every file written during one prompt is snapshotted first; `/undo` or `rooks undo` restores them all and
  deletes files the agent created. Commands cannot be undone (their receipts list what ran).
* **Hard budget.** `max_tokens` (default 200,000) and `max_usd` (default $1.00) per session. Before each model call Rooks
  estimates its input and stops rather than cross either cap. Local models cost $0. If a model's price is unknown the
  dollar cap cannot be enforced; Rooks says so at start and the token cap still applies (add the price under `[prices]`).
* **Project boundary.** File tools cannot read or write outside the project folder, nor write into `.git` or `.rooks`.
* **Receipts.** Each session writes `.rooks/runs/<session>.jsonl` (git-ignored automatically): one row per prompt, model
  call, tool call, approval decision and outbound connection, using the columns and outcome words of Workbench's `actions`
  table (`turn`, `model_call`, `tool_call`, `decision`, `egress`; `verified`, `denied`, `budget_paused`, ...). File edits
  are read back after writing and marked `verified` with a sha256. Text is redacted with Workbench's own patterns.
  `rooks receipts import --db /path/to/workbench.db` adds them to Workbench's run log (channel `rooks-code`), idempotently.
  `dry_run` is the one outcome Workbench does not produce itself.

## Configuration

`rooks init` writes `~/.config/ai-workspace/rooks.toml` (Windows: `%APPDATA%\ai-workspace\rooks.toml`; override with
`ROOKS_CONFIG`). Precedence: flags > `ROOKS_PROVIDER` / `ROOKS_MODEL` / `ROOKS_BASE_URL` > config file > defaults.
Keys are never stored: each provider names the environment variable that holds its key.

```toml
provider = "anthropic"
model = "claude-sonnet-4-5"
max_usd = 2.0
trusted_folders = ["~/code/sandbox"]

[providers.groq]                 # any OpenAI-compatible endpoint
kind = "openai"
base_url = "https://api.groq.com/openai/v1"
key_env = "GROQ_API_KEY"
model = "llama-3.3-70b-versatile"

[mcp.servers.github]             # stdio MCP servers; tools appear as mcp__github__<tool>
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_PERSONAL_ACCESS_TOKEN = "..." }   # or set it in your shell
trusted = false                  # true: its tools run without asking
```

A project's `.mcp.json` (`{"mcpServers": {...}}`, the format Claude Code and Cursor use) is picked up too. MCP tool
calls are asked about like commands. Only stdio servers are supported so far; HTTP/SSE servers are skipped with a notice.

## Limits (honest)

* Small local models (under ~7B) follow tool instructions unreliably; `qwen2.5-coder:7b` or larger is the practical floor.
* Replies are not streamed token by token yet (a spinner shows while the model works).
* No sandbox: approved commands run with your user's permissions. Approval, dry run and trusted folders are the controls.
* MCP over HTTP/SSE, prompt caching, and resuming a past session are not implemented yet.

## Development

```sh
cd rooks-code && python -m pip install pytest && python -m pytest -q
```

The end-to-end tests run the real `rooks` process against a scripted fake model served over HTTP (both the OpenAI and
Anthropic protocols) and a fake stdio MCP server, and import the receipts into a database built from Workbench's schema.
