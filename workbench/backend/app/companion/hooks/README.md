# Rooks Companion Hooks

**One command** connects your AI coding agents (Claude Code, Codex CLI, Gemini CLI) to the Rooks Companion — so you see live steps, approve permissions, and answer questions from the notch app, your phone, or the web.

## Quick Start

```bash
# Install hooks for all supported agents
python -m app.companion.hooks.install install --agent all

# Check status
python -m app.companion.hooks.install status --agent all

# Uninstall
python -m app.companion.hooks.install uninstall --agent all
```

That's it. The installer:
1. Copies the hook script to `~/.rooks/hooks/rooks_companion_hook.py`
2. Merges hook config into each agent's settings (with timestamped backups)
3. Tags entries so `uninstall` removes **only** our hooks, preserving yours

## What You Get

| Agent | Events Hooked | Live In Companion |
|-------|---------------|-------------------|
| **Claude Code** | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PostToolUseFailure, Notification, Stop, SubagentStop, SessionEnd | Steps (Read/Edit/Bash/Task/WebFetch), permissions, done/idle |
| **Codex CLI** | Stop, PermissionRequest, Notification | Steps, permission prompts (with allow/deny/always), done |
| **Gemini CLI** | SessionStart, BeforeAgent, BeforeTool, AfterTool, AfterAgent, Notification, SessionEnd | Steps, done/idle |

## How It Works

### The Hook Bridge (`rooks_companion_hook.py`)
- **Single file**, Python 3 stdlib only — no dependencies
- Reads hook JSON from `stdin`, maps events to Companion API
- **FAIL OPEN**: On any error (server down, file missing, timeout, bad input) → prints nothing, exits `0`
  - Never auto-allows permissions
  - Never blocks the agent
- Fire-and-forget step posts (≤1.5 s timeout)
- Permission requests: creates Companion request → long-polls `/wait` → returns decision to agent

### The Installer (`install.py`)
- `install [--agent claude|codex|gemini|all] [--home DIR]`
- `uninstall [--agent claude|codex|gemini|all] [--home DIR]`
- `status [--agent claude|codex|gemini|all] [--home DIR]`
- **MERGES** (never clobbers) into each agent's config
- Creates timestamped backups (`.backup.YYYYMMDD-HHMMSS`)
- Tags entries with `# rooks-companion-hook` for clean uninstall
- Idempotent: run `install` twice, get same result
- `--home` for testing (uses temp directory, never touches real configs)

## Configuration Details

### Claude Code (`~/.claude/settings.json`)
```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "~/.rooks/hooks/rooks_companion_hook.py claude SessionStart", "name": "rooks-companion-session-start-<tag>"}]}],
    "UserPromptSubmit": [...],
    "PreToolUse": [...],
    "PostToolUse": [...],
    "PostToolUseFailure": [...],
    "Notification": [...],
    "Stop": [...],
    "SubagentStop": [...],
    "SessionEnd": [...]
  }
}
```
**Docs**: [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)

### Codex CLI (`~/.codex/config.toml`)
```toml
[features]
codex_hooks = true

[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = "~/.rooks/hooks/rooks_companion_hook.py codex Stop # rooks-companion-hook <tag>"
timeout = 10

[[hooks.PermissionRequest]]
matcher = "Bash"
[[hooks.PermissionRequest.hooks]]
type = "command"
command = "~/.rooks/hooks/rooks_companion_hook.py codex PermissionRequest # rooks-companion-hook <tag>"
timeout = 10
```
**Docs**: Codex CLI uses `notify` (legacy) and `hooks` (newer) in `config.toml`. We use the `hooks` table with `Stop` and `PermissionRequest` events. [Example](https://github.com/pascal-gujer/codex-notify-hooks)

### Gemini CLI (`~/.gemini/settings.json`)
```json
{
  "hooks": {
    "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "~/.rooks/hooks/rooks_companion_hook.py gemini SessionStart", "name": "rooks-companion-session-start-<tag>", "timeout": 5000}]}],
    "BeforeAgent": [{"matcher": "*", "hooks": [...]}],
    "BeforeTool": [{"matcher": "*", "hooks": [...]}],
    "AfterTool": [{"matcher": "*", "hooks": [...]}],
    "AfterAgent": [{"matcher": "*", "hooks": [...]}],
    "Notification": [{"matcher": "*", "hooks": [...]}],
    "SessionEnd": [{"matcher": "exit", "hooks": [...]}]
  }
}
```
**Docs**: [Gemini CLI Hooks](https://geminicli.com/docs/hooks/)

## Unsupported / Known Limits

| Agent | Feature | Status |
|-------|---------|--------|
| **Claude Code** | `PermissionRequest` hook (distinct from `PreToolUse`) | Not separately hooked — `PreToolUse` covers permission flow; `Notification` with `permission_prompt` type fires but we treat as idle |
| **Claude Code** | `UserPromptExpansion`, `PreCompact`/`PostCompact`, `PreModelSwitch`/`PostModelSwitch` | Not hooked — no clear Companion mapping |
| **Codex CLI** | `notify` (legacy single-script) | Not used — we use the newer `hooks` table |
| **Codex CLI** | Other events (`SessionStart`, `PreToolUse`, `PostToolUse`, etc.) | Not documented in public Codex CLI hooks reference; only `Stop` and `PermissionRequest` are confirmed |
| **Gemini CLI** | `BeforeModel`/`AfterModel`, `BeforeToolSelection`, `PreCompress` | Not hooked — no clear Companion mapping |
| **All** | Streaming tool output / partial steps | Companion shows step on `PreToolUse`/`BeforeTool` (start) and `PostToolUse`/`AfterTool` (complete) |

## Testing

```bash
cd workbench/backend
# Full test suite (including new hook tests)
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY -u GROQ_API_KEY -u DEEPSEEK_API_KEY -u NVIDIA_API_KEY ~/.hermes/cache/scratch/aiw-venv/bin/python -m pytest tests/test_companion_hooks.py -v

# Or just the hook tests
~/.hermes/cache/scratch/aiw-venv/bin/python -m pytest tests/test_companion_hooks.py -v
```

### Test Coverage
- **Fail-open behavior**: server down, missing file, invalid input, timeout → exit 0, no output
- **Claude Code**: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop → correct events posted
- **Codex CLI**: Stop, PermissionRequest (allow/deny/always via long-poll)
- **Gemini CLI**: SessionStart, BeforeTool, AfterTool → correct events posted
- **Install/Uninstall**: Round-trip on temp HOME, preserves existing settings exactly, idempotent
- **Mutation checks**: Breaking changes would fail tests

## Files

```
workbench/backend/app/companion/hooks/
├── rooks_companion_hook.py   # The hook bridge (deployed to ~/.rooks/hooks/)
├── install.py                # Installer/uninstaller/status
└── README.md                 # This file
```

## Troubleshooting

### Hook not firing?
1. Run `python -m app.companion.hooks.install status --agent all` — verify installed
2. Check agent's settings file for our entries (search `rooks-companion-hook`)
3. Run agent with debug: `claude --debug` / check Codex logs / Gemini `/hooks panel`
4. Verify Companion is running: `~/.rooks/companion.json` exists with valid `url` and `token`

### Permission prompts not showing in Companion?
- Codex: `PermissionRequest` hook only fires when Codex is about to ask **or** would auto-deny in non-interactive mode
- Claude: Uses `PreToolUse` for permissions; `Notification` with `permission_prompt` fires but we map to idle
- Gemini: No explicit permission hook; `BeforeTool` can `deny` but no interactive prompt flow

### "Hook error" in agent output?
- Check Companion logs: `GET /api/companion/state` shows mood=requests
- Hook stderr is captured by agent — run hook manually with test input to debug

## Development

```bash
# Run hook manually for testing
echo '{"session_id":"test","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"ls"}}' | \
  python rooks_companion_hook.py claude PreToolUse

# Test installer on temp home
python install.py install --agent all --home /tmp/test-home
python install.py status --agent all --home /tmp/test-home
python install.py uninstall --agent all --home /tmp/test-home
```

## License

Part of Rooks — see root LICENSE.