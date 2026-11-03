# Your Claude plan through Claude Code (and your ChatGPT plan through Codex)

AI Workspace can hand coding jobs to **Claude Code**, Anthropic's own coding app, running on your computer and signed in
with **your** Claude Pro, Max, Team or Enterprise plan. The same works for OpenAI's **Codex** CLI with a ChatGPT plan.
Usage counts against your plan's limits, under your own agreement with Anthropic or OpenAI.

## What Anthropic allows, and why it works this way

* Anthropic's [Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance) page: third-party developers
  may **not** offer Claude.ai login or route requests through Free, Pro or Max plan credentials on behalf of their users,
  and should use API keys for their own products. A person **may** sign in to the unmodified Claude Code binary with
  their own plan.
* [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan):
  `claude -p` (headless Claude Code) and the Agent SDK run on your plan's usage limits.
* [Agent SDK overview](https://platform.claude.com/docs/en/agent-sdk/overview): products built on it should use API keys
  and must not offer claude.ai login.

So AI Workspace **never signs you in to Claude and never sees a token.** It finds the `claude` program you installed and
signed in to yourself, starts it in a folder you choose, and shows what it does. Sign-in, tokens and billing stay inside
Anthropic's unmodified app. AI Workspace does not use the Claude Code name for itself, does not modify or bundle the
binary, and does not use your plan as a chat model for the rest of the app: it hands Claude Code whole coding jobs that
you start yourself.

## Set up

```sh
npm install -g @anthropic-ai/claude-code    # Anthropic's installer: https://code.claude.com/docs/en/setup
claude                                      # choose "Claude account with subscription"; sign in opens in your browser
```

Codex (optional): `npm install -g @openai/codex`, then `codex login` and choose Sign in with ChatGPT.

The app's first-run screen (**Sign in with your subscription → Use your Claude plan through Claude Code**) and the
**Coding agent** page show whether each CLI is installed and signed in (from `claude auth status` / `codex login status`;
only the "signed in" flag and the kind of sign-in are read) with a **Check again** button.

## Use it

**In the app: Coding agent page.** Pick Claude Code or Codex, describe the job, pick a project folder and a mode:

| Mode | Claude Code | Codex |
| --- | --- | --- |
| Ask first (default) | `--permission-mode manual` | read-only sandbox |
| Edit files | `acceptEdits` (edits only; commands still ask) | `workspace-write` sandbox |
| Plan only | `plan` | read-only sandbox |

You watch the steps stream in (thinking, file edits, commands and their output). When Claude Code wants to do something
it was not allowed to, the run stops as **Waiting for your approval** and lists each step (the file it would create or
the exact command). **Allow and continue** resumes the same session with exactly those steps allowed (`--allowedTools
"Write,Bash(npm test)"`); **Don't allow** ends the run. Codex in Ask mode works read-only, then asks whether it may edit
files and continues in `workspace-write`. When the job ends you see the result and a diff of what changed (a `git diff`
of before/after snapshots in a git repo, otherwise built from the edits). Follow-ups continue the same session. Stop sends
Ctrl-C to the CLI. Every round is recorded in the run log (Runs) as channel `cli:claude` or `cli:codex`.

**In the terminal: Rooks Code.**

```sh
rooks -P claude-code "make the failing test pass"   # asks before each edit/command, like plain rooks
rooks -P claude-code --yes "..."                     # Accept-edits mode
rooks -P claude-code --dry-run "..."                 # Plan mode, changes nothing
rooks -P codex "..."                                 # Codex, read-only until you say yes
rooks -P claude-code                                 # interactive; follow-ups resume the same session
rooks doctor -P claude-code                          # installed? signed in with your plan?
```

## Privacy and safety

* Never a bypass: no `--dangerously-skip-permissions`, `bypassPermissions` or Codex full access.
* The CLI gets your environment **minus** `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` (Claude) or
  `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL` (Codex), so a key the app holds is never used in place of your
  sign-in. If you signed the CLI in with your own API key instead of a plan, the app says so.
* Credential files (`~/.claude/.credentials.json`, the macOS keychain item, `~/.codex/auth.json`) are never read.
* Claude Code loads the chosen folder's own settings, hooks, `CLAUDE.md` and `.mcp.json`. Only use folders you trust.
* The folder must exist and cannot be the drive root.
* Set `WORKBENCH_CLAUDE_BIN` / `WORKBENCH_CODEX_BIN` (app) or `ROOKS_CLAUDE_BIN` / `ROOKS_CODEX_BIN` (Rooks) to use a
  specific binary.

## Limits

* Chat, research, automations and the browser agent still need a model from Settings (free cloud key, API key, ChatGPT
  plan or local Ollama). The Claude plan powers coding jobs only, on purpose (see the terms above).
* Coding jobs need the app running on your own computer (desktop app or local install), where the CLI is installed and
  signed in. A shared or hosted server would run the server owner's CLI, so don't expose it there.
* Rooks' `/undo` and receipts don't cover delegated jobs; use git.
* Tested with a scripted stand-in for both CLIs (`workbench/backend/tests/fixtures/fake_claude`, `fake_codex`, which
  speak the real stream shapes) and the real CLIs in their signed-out state; not yet with a live signed-in plan.
