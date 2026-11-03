# AGENTS.md: working on AI Workspace

AI Workspace is a personal AI assistant you run yourself: a web app (workbench) also shipped as a desktop app window,
plus three command line agents: `rooks` (terminal coding agent), `rooks-browser` (browser agent), `rooks-desk`
(computer use). All three can use the app's models, keys and plans through its OpenAI-shaped `/v1` bridge.

## Layout

- `workbench/backend/app/` FastAPI app (Python 3.12). `main.py` wires routers. Models: `llm.py`, `adapters.py`
  (one class per protocol, `CliPlan` runs the user's own `claude`/`codex`/`copilot` CLI), `router.py` (Auto).
  `/v1` bridge: `bridge.py`; terminal link (memory, skills, search): `rooks_link.py`. Mail: `mail.py` (IMAP/SMTP),
  `cloud_mail.py` (Gmail and Microsoft Graph), `connect.py` (Google/Microsoft OAuth sign-in), `mail_autodetect.py`.
  Database and migrations: `db.py` (SQLite, schema version in `SCHEMA_VERSION`).
- `workbench/backend/tests/` pytest suite (about 600 tests, about 5 minutes).
- `workbench/frontend/src/` React + Vite + TypeScript. Pages in `pages/`, shared helpers in `util.tsx`, API client in
  `api.ts`, styles in `styles.css` and `connect.css`.
- `rooks-code/rooks_code/` the `rooks` terminal: `agent.py` (loop), `tools.py` (file and command tools), `extras.py`
  (todo, web, background jobs, sub-agents, project instructions), `learning.py` (memory and skills shared with the
  app), `tui.py` (interactive terminal), `cli.py` (commands, plain mode), `config.py`, `providers.py`.
- `rooks-browser/`, `rooks-desk/` the browser and computer agents. `desktop/` launcher and native shell.
- `install.sh` installs everything; `scripts/test-all.sh` runs every suite.

## Commands

- Everything: `./scripts/test-all.sh` (needs uv and npm). Must end with "All passed." before pushing.
- Backend only: `cd workbench/backend && env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u NVIDIA_API_KEY ~/.venvs/aiw/bin/python -m pytest -q -p no:cacheprovider -W ignore`
  (provider keys unset, or tests call real providers).
- Rooks only: `cd rooks-code && ~/.venvs/rooks/bin/python -m pytest -q -p no:cacheprovider`
- Frontend: `cd workbench/frontend && npx tsc --noEmit -p . && npm run build && npm test`

## Rules

- Branch `integration/v0.3` is the working line; `main` is stale. Do not merge to `main` unless the owner says so.
- Every user-facing string is plain words for non-technical people: no URLs, ids, "cli://", "OAuth", "MCP", "UTC",
  "Workbench" or stack traces on normal screens. Technical detail only under "Advanced".
- The terminal, the browser and the app must match: same features, names and behaviour. A feature added in one is
  added (or reachable) in the others.
- Never fake results. A test that guards a fix must fail on the old code first.
- Secrets never go in logs, commits or chat. Keys live in the app; the terminal gets them from `app.json`.
- Mail connects through the provider's own sign-in (Google, Microsoft) or app passwords; never scripts another app.
