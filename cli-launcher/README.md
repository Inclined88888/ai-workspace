# Official CLI launcher add-on (Item 116)

This optional service hands a task to the unmodified official Claude Code CLI,
version **2.1.308**. It is not a model provider, OpenAI-compatible endpoint, or
subscription credential bridge. Node and Claude Code stay out of the core image.
The transport is dependency-free and command settings are supplied by deployment,
so Item 117 can reuse `server.mjs` without editing it.

## Enable and sign in

```sh
# --with replaces the saved add-on list; include any other add-ons you use.
./setup.sh --with claude-code
# Interactive terminal: complete Anthropic's own sign-in flow.
docker compose run --rm --no-deps claude-code claude auth login
docker compose exec claude-code claude auth status
```

The official CLI owns its login state in the `claude-code-auth` volume. The
launcher never reads credential files or returns identity fields from auth status.
Do not copy host credentials or subscription tokens into `.env`. No provider API
keys are forwarded. A run requires both successful `claude auth status` exit and
JSON `loggedIn: true, authMethod: "claude.ai"`; API-key login is refused.
Login does not restart the service and is rechecked before every task.

`./setup.sh --with none` disables add-ons and clears `CLAUDE_CODE_URL`. Volumes
survive disabling and `docker compose down`. To sign out, use
`docker compose exec claude-code claude auth logout`. Deleting the auth volume
removes its saved login; deleting the workspace volume removes its files.

## Internal API

The service has **no published host port**. Call it from a trusted stack client
at `http://claude-code:8110`, using the generated `CLAUDE_CODE_API_KEY` as a bearer
key. Workbench receives `CLAUDE_CODE_URL` and `CLAUDE_CODE_API_KEY`, following the
browser add-on config pattern. This item adds no model-provider slot or automatic
assistant delegation; callers must explicitly choose this CLI hand-off.

* `GET /health`: liveness only, no authentication or CLI invocation.
* `GET /auth/status`: bearer required; returns only `{"authenticated":true|false}`.
* `POST /run`: bearer required, `Content-Type: application/json`, body
  `{"task":"Explain the project in this workspace"}`. Returns `{"output":"..."}`.
  Output is delivered only in that response and is never logged or persisted by
  the launcher. Treat it as private content; callers must not log response bodies.

Example from inside Workbench (does not print the bearer key):

```sh
docker compose exec -T workbench python - <<'PY'
import os
import httpx
r = httpx.post(os.environ['CLAUDE_CODE_URL'] + '/run',
    headers={'Authorization': 'Bearer ' + os.environ['CLAUDE_CODE_API_KEY']},
    json={'task': 'Reply with hello'}, timeout=250)
print(r.status_code)
# Consume r.json()['output'] privately; do not log task or output content.
PY
```

The CLI receives the task over stdin, fixed `--print --output-format text
--no-session-persistence` arguments, and a dedicated workspace volume. Requests
cannot supply commands, flags, environments or working directories. Claude Code's
normal permissions apply; this launcher never adds a permission-bypass flag.
Tools requiring interactive approval may be refused in print mode. This is not a
host-file or computer-control integration.

Limits: one request at a time (including auth checks), 64 KiB request body,
1 MiB combined stdout/stderr per subprocess, 120 seconds per subprocess (auth
check plus task can take up to 240 seconds). Busy/shutting down returns 503;
unsigned-in returns 409; subprocess failure/timeout/overflow returns a generic
502 without CLI stderr. Client disconnect and server shutdown terminate the
process group, escalating from SIGTERM to SIGKILL after one second. Compose uses
`init: true` to reap orphaned helpers. No task queue or retained job history.

## Privacy and versioning

`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` is set for the add-on and the CLI
child; `DISABLE_AUTOUPDATER=1` preserves the version pin. There is no launcher
analytics, access logging, credential parsing, session scraping, `--bare`, or
alternate provider HTTP client. Claude Code itself connects to Anthropic for
login and inference; this is an explicit cloud add-on and does not provide
local-only inference. The CLI manages its own on-disk state; the no-log promise
here covers the launcher, not upstream internals.

The Docker build fails if the pinned package is unavailable. Do not substitute a
version silently. Before approving a version update, verify its official auth
status JSON/exit behavior, print flags, telemetry controls and login flow.

## Validation

```sh
node --test cli-launcher/test/*.test.mjs
bash -n setup.sh
APP_SECRET=placeholder-at-least-32-characters TOOLS_API_KEY=placeholder-at-least-24-characters \
  docker compose --profile claude-code config --quiet
docker build -f cli-launcher/Dockerfile.claude cli-launcher
```

The tests use a fake subprocess; they never sign in or spend subscription quota.
CI also builds the pinned CLI image. A maintainer must validate official login,
auth status and one real prompt with their own subscription before merge.
