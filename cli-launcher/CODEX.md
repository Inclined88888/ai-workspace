# Codex CLI launcher (Item 117)

Enable with `./setup.sh --with codex` (include other desired add-ons in the list).
Authenticate interactively with `docker compose run --rm --no-deps codex codex login --device-auth`.
The official CLI alone owns the login state in the `codex-auth` volume. No host
credential directory is mounted and no launcher code reads a credential store.
Use `codex logout` through the same service to sign out.

API: `http://codex:8110`, bearer `CODEX_API_KEY`, same `/run`, `/auth/status` and
`/health` contract and limits as [the generic launcher](README.md). Workbench
receives `CODEX_URL` and `CODEX_API_KEY`; this is an explicit hand-off, with no
automatic assistant delegation. Disable through setup by omitting `codex`.

The generic `server.mjs` is unchanged from Item 116. A small adapter translates
Codex's text status into the JSON boolean expected by that server. It invokes
`codex login status`, ignores stdout, bounds stderr and accepts only successful
exit plus the exact ChatGPT login message. Unknown/API-key statuses fail closed.
It never returns the original status text. Task runs use `codex exec` with stdin,
`--ephemeral`, a read-only sandbox, and no permission bypass. Only task stdout is
forwarded; stderr is discarded. Both processes share the launcher's process group
for termination. The workspace volume is isolated from the host.

## Evidence and privacy controls

Pinned official package: `@openai/codex@0.163.0`, matching the CLI inspected locally.
Commands inspected: `codex login --help`, `codex login status --help`, and
`codex exec --help`. The last documents stdin, ephemeral runs and sandbox modes.

Official sources:
- [Login status implementation](https://github.com/openai/codex/blob/main/codex-rs/cli/src/login.rs)
- [Configuration schema](https://github.com/openai/codex/blob/main/codex-rs/core/config.schema.json)
- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)

No documented universal telemetry-disable environment variable was established.
Do not invent one or rely on `DO_NOT_TRACK`. The shipped user configuration and
adapter command-line overrides set `analytics.enabled=false`, `feedback.enabled=false`,
`otel.exporter="none"`, `otel.trace_exporter="none"`, and
`otel.metrics_exporter="none"`. The schema explicitly states that disabling analytics
does not disable custom OTLP metrics exporters, so all three exporters are disabled.
The child environment whitelist excludes inherited exporter settings and API keys.
Interactive login uses the shipped configuration. Users must not enable telemetry
in that volume's config. Authentication/inference remain explicit cloud traffic;
packet-level absence of telemetry needs owner validation.

No subscription login or paid inference was performed during implementation.
Before acceptance, build the image, sign in through Codex, run one `/run` task,
check only the task result is returned, and verify telemetry is absent on egress.
The isolated environment could not build Docker images in this session; CI builds
both pinned images. Rollback: disable the profile; volumes remain until explicitly
removed. No core database migration is needed.
