# DeepSeek Harness: what it is, what ROOKS takes from it, and why not more

Evaluated on 28 October 2026 from the source at `github.com/deepseek-ai/deepseek-harness`, commit `f3803c9`
(package version `0.2.2-alpha.2`, MIT licence). Statements below come from its code and its own documents, cited by path.

## What it is

- **Stack:** TypeScript on Node 24.9 or later (`package.json` `engines`), pnpm 11, about 50 packages under `packages/`,
  apps for CLI, web, Electron desktop (`apps/desktop`, `electron ^44.7.0`) and a desktop host.
- **Cordis, everything a plugin.** The model adapter, tool registry, session log and the agent loop itself are plugins
  contributing services, typed events and reversible effects to a shared context (`docs/architecture.md`). Profiles
  (`web`, `headless`, `sdk`, `sdk-minimal`, `acp`) compose ordered bundles; `dsh-base` holds the shared services.
- **Session log as the source of truth.** An append-only event log (`turn/*`, `step/*`, `tool/*`...), versioned JSONL
  with one migration package per format step; "every model request must be reconstructable from the log".
- **Approvals** (`packages/interaction/user-approval/src/index.ts`, verified): a closed outcome set
  `allowed-once | rejected | cancelled | unavailable`; anything unexpected, a missing answerer, or a throwing answerer
  becomes `unavailable`, and callers deny unless the outcome is `allowed-once`. Each request has its own id pairing
  an `approval/asked` and `approval/decided` audit record. Per-session policy `ask` or `never`.
- **Background jobs** (`docs/subsystems/jobs.md`): status `running | stopping | completed | killed | failed`; durable
  `job/registered` and `job/settled` records; "a missing settlement remains incomplete... consumers must not invent a
  finish timestamp".
- **Web client** (`docs/subsystems/web-client.md`): Host owns authoritative state; every stream generation opens with a
  complete baseline, then increments; on reconnect the client replaces its model from the new baseline; ordinary
  notifications are not replayed.
- **Desktop** (`apps/desktop/README.md`): Electron loads the packaged web app, starts the Host as a child process bound
  to `127.0.0.1` on an OS-assigned port, and attaches credentials only to requests from its own window.

## Maturity and safety, in its own words

- `SAFETY.md`: "experimental developer-preview software. It has not undergone a security audit and must not be
  treated as secure or production-ready."
- `packages/boot/plugin-manager/README.md`: installed plugin code "executes in-process outside the workspace sandbox".
- Ten upgrade guides from `v0.1.7-rc.2` to `v0.2.1-rc.4` (`docs/upgrade-guide/`): the format and APIs are still moving.

## The three options

| | A. Keep FastAPI and React, adopt selected ideas | B. Adapter to reuse its tools | C. Replace backend parts with it |
|---|---|---|---|
| What it means | Same Python engine; borrow patterns | Run `dsh --profile sdk` as a sidecar and call its tools over JSON-RPC | Move turn loop, tools or sessions to Cordis |
| New runtime per install | none | Node 24 and an alpha package | Node 24, and a rewrite |
| Permission systems | one (ROOKS) | two: its tools carry their own sandbox and approval policy, so ROOKS approvals no longer cover everything | one, but not ROOKS's, and unaudited by its own statement |
| Data | unchanged | unchanged, plus its session store | migration of every conversation, mission, approval |
| Risk | low | medium: version churn, two audit trails | high: rewrite of working, tested code (309 backend tests, 6 browser suites) |
| Maintenance | ROOKS only | ROOKS plus tracking an alpha API | Cordis expertise for every change |

**Decision: A.** It is the only option that keeps one permission system, one audit trail and the existing data, and
it adds no runtime. B fails the "one engine" requirement: a tool run inside the sidecar would be judged by its own
approval policy, not ROOKS's. C has no evidence of lower complexity: ROOKS's turn loop, approvals and boundary are
already shared by every engine that calls tools (see `docs/ARCHITECTURE.md`), and the harness says it is not ready to
be trusted. Interoperation, if wanted later, should go through MCP, which both projects already speak, not through
code-level adapters.

## Ideas adopted (and where)

| Idea from the harness | In ROOKS v15 |
|---|---|
| Closed, fail-closed approval outcomes; a grant covers one action | Already true since v14 (`decide_approval`); v15 records `asked` and `decided` as audit events with the approval id |
| Closed job status; durable registered and settled records; never invent a finish | `work.py`: one typed lifecycle over the existing engines; `work_events`, written by database triggers on every status change, so a transition cannot go unrecorded |
| Every stream opens with a baseline, then increments; reconnect replaces | `/api/v1/events`: first frame is the full state (work, review count, conversations cursor); clients replace their copy on reconnect |
| Capability seam: definition, provider, consumer | `/api/v1/capabilities`: each tool with its group, risk, timeout, whether it needs approval, and whether its provider is set up |
| Desktop: Electron around the same web app, host on 127.0.0.1, credentials only for its own window | `desktop/` (see `docs/DESKTOP.md`) |

## Not adopted

- Installing plugins at run time. Code that runs in-process outside the sandbox cannot be made safe by a trust prompt.
  ROOKS keeps skills as text procedures (approved before saving) and add-ons as separate containers.
- The event-sourced session log as the store. ROOKS's SQLite tables already back every client; a second store would
  split the truth.
