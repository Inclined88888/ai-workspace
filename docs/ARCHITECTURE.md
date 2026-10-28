# How ROOKS works (as built)

This describes the code on `development/rooks-v15`, verified by reading it, not the intended design. Paths are under
`workbench/backend/app/` unless stated.

## One request, end to end

```
browser / CLI / Telegram / desktop
   │  POST /api/assistant/chat  (session cookie, or the terminal token)          assistant_api.py
   ▼
assistant.run_turn(Ctx, message)          one turn loop for every channel                assistant.py
   │  model call ──► llm.py (provider, AI mode guard, spending limit, fallbacks)
   │  tool call  ──► run_tool ──► validate_args ──► permissions.mode / workspace.approval_reason
   │                     │                         └─ needs approval ──► request_approval ──► approvals row
   │                     └─ scope.py (project boundary), TIMEOUTS, audit.record
   │  start_research / start_mission / run_coding_agent ──► coordinator.register (a job)
   ▼
SSE events (start, tool_call, tool_result, approval, message, done) back to the caller

Needs your review (attention.py) lists approvals ──► POST /api/approvals/{id} ──► decide_approval
   └─ rebuilds Ctx from the recorded scope; refuses tools outside it and spent budgets; runs once (atomic claim)

coordinator.py (delivery loop) ──► posts research reports, mission plans and results, coding results
                                   into the conversation that started them, never during a turn
```

## Execution engines

| Engine | Module | Runs through the turn loop? | Status values (native) |
|---|---|---|---|
| Chat, Telegram, schedules, check-ins | `assistant.py`, `telegram.py`, `scheduler.py`, `checkins.py` | yes | (turns, no status) |
| Company agents | `company.py` | yes (`assistant.ask`) | todo, queued, running, done, failed, blocked |
| Missions | `missions.py` | yes, each step (`run_turn`) | planning, review, queued, running, waiting_approval, paused, done, partial, failed, cancelled |
| Workers | `workers.py` (on missions) | yes (`run_turn`) | queued, running, waiting_approval, in_review, needs_you, blocked, done, failed, cancelled |
| Deep research | `research.py` | no: own search, read and review rounds calling `llm` directly | running, done, error, cancelled |
| Coding agents | `coding.py`, `companion/rooks_companion/agents.py` | no: the official CLI on the paired computer | running, done, failed, cancelled |
| Computer Use | `computer.py`, the companion | no: actions queued for the companion | preparing, queued, dispatched, done, failed, refused, cancelled, uncertain |

Every engine that calls tools does so through `run_tool`, so permissions, approvals, the project boundary, timeouts and
the audit log are shared. Research, coding and Computer Use have their own runners; they are started from tools, and
their results come back through `coordinator.py`.

`work.py` (v15) maps the native statuses above onto one typed lifecycle (see "Work lifecycle"). The engines keep their
own state machines; nothing was renamed.

## Approvals

- Created only by `request_approval`, with the tool, its arguments (encrypted for Computer Use), the reason and the
  scope (requester's tools, project, mission, task, channel).
- Decided only by `decide_approval(id, approve)`; the request body is a yes or no, so the action cannot be changed at
  decision time. The row is claimed atomically (`pending` → `running`), so two clients deciding at once run it once.
- Missions and workers then resume; an action already approved and done is replayed from the mission's record
  (`missions.replay`), not executed again.

## Authentication

| Caller | Credential | Routes |
|---|---|---|
| Browser | session cookie (`security.check_session`) | everything under `/api` |
| Terminal CLI, desktop | bearer token from Settings, stored as a SHA-256 hash (`cli_token_hash`) | `assistant_api.py` routes and, from v15, `/api/v1` |
| Open WebUI, MCP clients | `TOOLS_API_KEY` | the tool server only |
| Companion | its pairing credential | `computer_api.companion` |

## Storage

One SQLite database (`db.py`), schema migrations by `_add_column` and `CREATE TABLE IF NOT EXISTS`, so an older
database upgrades in place on start. Uploaded files under the data folder (`files.py`), with versions.

## What is separate on purpose

Add-ons run as their own containers and are linked, not embedded: Open WebUI, Paperclip, Hermes, the browser agent,
SearXNG, Ollama. The companion runs on your computer and is the only thing that touches it.
