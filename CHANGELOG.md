# Changelog

Every user-visible change that needs action, or changes data on upgrade, gets an entry: date, version, breaking changes,
migration steps, rollback steps. Workbench has no release numbers yet (BACKLOG). Entries are identified by schema
version and the merge commit on `main`.

## 2026-10-30 · Sunshine theme and Claude plan via Claude Code · branch feat/sunshine-claude

- **Sunshine theme (new default)**: bright, warm and airy: near-white warm background, soft cards, a clean sans
  (Hanken Grotesk) throughout, pill buttons and a #FFC83D accent used sparingly (text uses an AA darker amber, #8A5A00).
  Paper, Linen, Gallery and Night stay in Appearance, and a saved choice is kept on upgrade. Home, chat, settings,
  first run and the browser agent page are decluttered. Screenshots: `screenshots/sunshine/`.
- **Your Claude plan through Claude Code**: first run offers "Use your Claude plan through Claude Code" with install and
  sign-in status. The new **Coding agent** page runs your own signed-in `claude` (or `codex`) headless in a folder you
  pick: live steps, approvals per edit/command, follow-ups in the same session, and a diff. The app never signs you in or
  reads a token. API: `/api/cli-agents`. See [docs/CLAUDE-CODE.md](docs/CLAUDE-CODE.md).
- **Rooks Code**: `-P claude-code` and `-P codex` delegate to those CLIs with Rooks-style approvals.
- Migration: none. Rollback: pick Paper in Appearance to get the previous look.

## 2026-10-30 · Release candidate v0.1 · branch release/v0.1-rc (all feature branches combined)

- **One-command install without Docker**: `install.sh` (macOS, Linux) and `install.ps1` (Windows) install `ai-workspace`,
  `rooks` and `rooks-browser` for the current user. uv brings Python 3.12. When the source has no prebuilt web UI and the
  computer has no Node.js, the installer downloads Node.js 22 from nodejs.org, checks its SHA-256, builds, and removes it.
  See [QUICKSTART.md](QUICKSTART.md).
- **Launcher**: the window opens on a one-time sign-in link served by the app (one use, two minutes), so the launch token
  is never on a command line or in a URL. This fixes a blank "Your file couldn't be accessed" (ERR_FILE_NOT_FOUND)
  window on macOS caused by an earlier temporary redirect file. It waits for the app and checks the web UI loads, logs to
  `logs/launcher.log`, and has `--logs`; `--status` also shows the UI check, version and logs folder. The installer's
  Applications / Start menu shortcut is no longer mistaken for the native app and now has the real icon.
- **Agents**: `rooks` and `rooks-browser` accept `GROQ_API_KEY` and `GEMINI_API_KEY` directly, and explain how to set up a
  model when none is available. rooks-browser on Groq backs off on rate limits, trims long pages to fit, and recovers
  malformed replies.
- **Browser agent page**: live picture while a run works, a screenshot per step, the agent's thinking and actions, and
  Allow / Deny for paused sensitive steps.
- **CI**: `macOS window` workflow (macos-14) installs with the one-command install and checks the window shows the UI.
  The desktop build falls back to ad-hoc signing when the Apple certificate secret cannot be imported.
- **Local models**: the first chat works with whichever Ollama model is installed; Rooks Code prefers an installed
  coder model when Ollama is the only provider; one answer is capped at `OLLAMA_NUM_PREDICT` (4096) tokens; the assistant
  only offers `web_search` when a search provider is set up.
- **Rooks Code**: more robust parsing of tool calls written as text by small models (several JSON objects in one block,
  duplicates run once, mixed native and text calls).
- **CI**: new `mobile` job and an `installer` job that runs the one-command install on Ubuntu, macOS and Windows.
- **Data / migration**: none. **Rollback**: reinstall from `main`.

## 2026-10-30 · Cloud by default: "Choose how to think", Sign in with ChatGPT, smart router · branch feat/cloud-models (builds on feat/skills-and-motion)

- **First run is now "Choose how to think"**, shown on the desktop app's first start and in Docker whenever no model is connected. It offers four options:
  - **Free cloud** (recommended): a guided Gemini or Groq key. It opens the key page, takes the key by paste or from the clipboard, recognises the provider and checks the key before saving it.
  - **Sign in with your subscription**: ChatGPT, through OpenAI's official open-source flow. Claude, SuperGrok and Google AI show "not available" with the reason and a link to the source.
  - **Paste API keys**: OpenAI, Anthropic, xAI, Google, Groq, OpenRouter, Mistral, DeepSeek, or any OpenAI-compatible URL.
  - **Local Ollama**, marked advanced.
- **Smart router.** `auto` (also `auto:fast` and `auto:strong`) is the default model once any cloud provider is connected.
  - Each turn uses a fast or a strong model depending on the task.
  - On a 429, a quota or credit error, a 5xx or an outage, the router moves to the next provider.
  - A provider that hits its daily limit is parked until its reset.
  - Each answer shows who replied and how many free requests are left.
  - The new **Models** page (`/models`) sets the order, the models per tier and the local fallback, and shows quota meters.
  - Chat, the assistant, research, Rooks Code and Rooks Browser all use the router (`-P workbench -m auto`, `X-Workbench-Channel`).
- **New presets**: Groq, xAI, Mistral and DeepSeek. Setup's `GROQ_API_KEY` and `XAI_API_KEY` are now seeded as connections.
- **API.** New endpoints:
  - `/api/providers/detect` and `/api/providers/validate`
  - `/api/router` and `/api/router/reset/{id}`
  - `/api/auth/subscriptions`
  - `/api/auth/chatgpt/*`
  - `/api/status` gains `needs_models`.
- **Data / migration.** One new table is created on startup: `router_state` (additive). The router settings are stored in the `router` setting. Existing default models are not changed; `auto` becomes the default only when no default was set.
- **Rollback.** Check out `feat/skills-and-motion`. The old build ignores `router_state`, but a default model of `auto` has to be changed back in Settings.
- **Docs**: `docs/models-and-router.md`, and `docs/hosted-free-tier.md`, which is a design only.
- **Tests.** The backend has 212 passing tests, including:
  - router fallback with fake 429, daily-limit and 5xx providers
  - stream fallback and quota headers
  - onboarding and key validation
  - Sign in with ChatGPT end to end against a fake OAuth server
- **Bundle.** JS is 464.11 kB (137.34 kB gzip) and CSS is 56.48 kB (11.83 kB gzip).
- **Screenshots** are in `screenshots/models/`.

## 2026-10-30 · Signature motion, simple appearance controls, secure GitHub skills · branch feat/skills-and-motion (builds on feat/premium-ui)

- **Tracer.** While the assistant, a research job, a browser-agent task or a run is working, a thin cherry line with an ink tip travels around the composer or card border. It is drawn as an SVG rect with `stroke-dashoffset` on the compositor, so it adds no layout work.
- **Motion.** Added page-turn route transitions, staggered cards, press and hover feedback, and nav dots.
  - There is a new **Motion** setting: Full, Subtle (slower tracer, fades only) or Off (a static 1px line, no animation).
  - The OS "reduce motion" setting always wins.
- **Appearance page** (`/appearance`, also in the sidebar foot and the Cmd/Ctrl-K palette) has these controls, all with a live preview:
  - Theme: Paper, Linen, Gallery or Night
  - Typeface: Editorial, Modern, Classic or System
  - Text size: 12–18 px
  - Spacing: Comfortable or Compact
  - Motion
  - Accent: Cherry, Ink, Cobalt, Forest or Ochre

  Settings are stored in the browser as `wb-appearance`. The old `wb-theme` value migrates automatically ("dark" becomes Night). The sidebar Light/Dark switch is gone; the palette has "Paper theme", "Night theme" and similar commands.
- **Simpler navigation.** The sidebar now has three groups:
  - Primary: Home, Chat, Tasks, Notes, Calendar, Mail
  - Agents: Automations, Skills, Research, Browser agent
  - A collapsible **More** group (remembered as `wb-nav-more`) holding the admin pages
- **Skills page** (`/skills`; the old `/assistant-skills` redirects to it).
  - Add a skill or MCP server from a GitHub link, or search GitHub.
  - Every candidate passes an 8-stage gate: pinned fetch, code scan, prompt-injection scan, permission manifest, sealed trial run, risk verdict, your per-permission approval, and run-time enforcement with an audit log.
  - High risk is blocked unless you type `install <name> anyway`.
  - Updates re-run the gate and show a diff. Access an update newly asks for is never pre-ticked.
  - Installed skills can be turned off or uninstalled. Blocked run-time actions become requests under **Activity**.
  - Installed skills are available to the assistant (`load_skill`, `run_skill_script`) and to Rooks Code over MCP.
  - Details and known gaps: `docs/skills-security.md`.
- **Data / migration.** Three new tables are created on startup (`hub_skills`, `skill_audit`, `skill_permission_requests`; additive, `CREATE TABLE IF NOT EXISTS`). Skill files live in `DATA_DIR/skills/{staging,installed}`. No existing data changes.
- **Rollback.** Check out `feat/premium-ui`. The new tables and `DATA_DIR/skills` are ignored by older builds and can be deleted.
- **Tests.**
  - Backend: 197 pass, including 30 skill-gate tests in which every malicious fixture comes out High and is refused, and the benign ones come out Low and install.
  - Frontend: `npm test` runs 6 Appearance unit tests with Node's built-in runner.
- **Bundle.** JS is 444.43 kB (132.37 kB gzip) and CSS is 51.25 kB (10.93 kB gzip).
- **Screenshots** are in `screenshots/skills/`, including `tracer-composer.mp4` and `tracer-composer.gif`.

## 2026-10-30 · iPhone and Android companion app (phase 4) · branch feat/mobile-app (builds on feat/premium-ui)

Workbench side (pairing, API, push hook). The app itself is in `mobile/`; its lines follow.

- **Pairing.** Settings → Phone shows a QR code and an 8-character code (5 minutes, single use, stored hashed). The phone exchanges it for a device token (`wbd_…`, stored hashed; kept in the phone's Keychain/Keystore). Settings lists paired phones and unpairs them. Ten wrong codes per address per 5 minutes are refused.
- **Scoped phone API** under `/api/mobile/*`: home glance, approvals approve/deny, runs and traces, tasks, notes, conversations, model list, chat (streamed, channel `mobile`), photo and voice attachments, push token. A device token cannot reach Settings, keys, mail, the password or other phones. Decisions from the phone are logged with channel `mobile`.
- **On your Wi-Fi.** Desktop app: "Phone access on this Wi-Fi" starts a listener on `0.0.0.0:8766` (`WORKBENCH_MOBILE_PORT`) that serves only `/api/mobile/*` and `/healthz`; remembered across restarts. Docker: `BIND_ADDRESS=0.0.0.0` plus `MOBILE_URLS`.
- **Away from home.** A relay address (Tailscale, Cloudflare Tunnel, or a future relay) in Settings or `MOBILE_RELAY_URL` is offered to the phone after the Wi-Fi address. See `docs/mobile-pairing.md`.
- **Push (off by default).** "Approval needed" and "Run finished" through Expo's push service, only when Settings → Phone → Push notifications is on and a phone registered a token. Needs Expo/APNs/FCM credentials in the app build.
- **New tables** `devices` and `pairing_codes` (created automatically; no data migration, schema version unchanged). New dependency `segno` (QR codes, pure Python). Rollback: run the previous build; the two tables are ignored.
- Screenshots: `screenshots/mobile/settings-phone-*.png`.

The app (`mobile/`, Expo SDK 58, React Native, TypeScript, Expo Router; see `mobile/README.md`):

- **Screens.** Today (what needs you, an ask box, jobs on the desk, tasks, notes), Chats, Chat (streaming-style reveal, Markdown and code blocks with copy, tool receipts, inline approvals), Approvals (one-tap Approve/Deny, tab badge), Runs (live status, "Needs a look", receipt with trace), Settings (paired computer, model, Paper/Ink/System, haptics, notifications, unpair) and Pair (QR scan, typed address and code, `aiworkspace://pair` deep link).
- **Compose from the phone.** On-device dictation (development build), photo from camera or library, image/audio files (10 MB); attachments go to the computer and are referred to in the message.
- **Security.** Device token in SecureStore (Keychain/Keystore, this device only); tokens revocable from either side; every chat and decision from the phone is logged as a receipt with channel `mobile`; chat sends an Idempotency-Key; no secrets or credentials in the repo.
- **Push.** Expo push for "Approval needed" and "Run finished"; tapping opens the approval or run. Needs an EAS project id plus APNs/FCM V1 credentials (documented).
- **Look.** The editorial redesign on the phone: warm paper `#F5F1E8` and ink, Bodoni Moda and Hanken Grotesk, cherry red `#D21F3C`, baby-blue fills, hairline rules, square corners, fade/lift page turns and haptics. Optional dark (Ink).
- **Builds.** `eas.json` profiles for a development client, an Android preview APK and store builds; README covers Expo Go, TestFlight, Play internal testing and store release (Apple US$99/year, Google Play US$25 once).
- **Checks.** 37 jest tests (pairing links, SSE parsing, Markdown, reveal, turn reducer, API client), typecheck, lint, `expo-doctor` 20/20, web export. Phone screenshots: `screenshots/phone-*.png`.

## 2026-10-30 · Editorial UI redesign (light-first) · branch feat/premium-ui (builds on feat/desktop-app)

Replaces the dark, gradient "premium" look with a quiet 90s fashion-editorial style. The structural work from the premium pass is kept: command palette, code blocks, skeletons, streaming and accessibility.

- **Light by default.** Warm paper (#F5F1E8) and near-black ink (#141311). Dark mode is an opt-in toggle (Light / Dark in the sidebar or the palette). It is stored in `wb-theme` and no longer follows the OS setting.
- **Type.** Bodoni Moda (Didone, optical sizes) for headings and the wordmark, Hanken Grotesk for the UI and JetBrains Mono for code. All three are bundled locally (OFL 1.1). Inter has been removed.
- **Colour.** One accent, cherry red (#D21F3C; #B5122B for text), and a baby-blue secondary (#D5E5F4) used only for fills. Every text pair meets WCAG AA.
- **No gradients, glow, blur or sparkle icons.** The logo mark is now a typographic wordmark. Surfaces are flat with hairline rules and square corners, and nav items are text only.
- **Motion** is limited to fades and small page turns, and is fully disabled under reduced motion.
- **Desktop splash and welcome screen** were restyled to match. The splash bundles its own Bodoni subset (116 KB).
- **Bundle:** JS 410.07 kB (122.45 gzip), CSS 37.41 kB (8.13 gzip).
- Screenshots are in `screenshots/editorial/`.

## 2026-10-30 · Premium UI redesign + animations · branch feat/premium-ui (builds on feat/desktop-app)

* New design system in `workbench/frontend/src/styles.css`: tokens for colour, type, spacing, radius, elevation and
  motion, with light and dark themes checked for WCAG AA contrast. The theme follows the OS; a System / Light / Dark switch
  in the sidebar overrides it (stored in the browser as `wb-theme`).
* Inter and JetBrains Mono are bundled (`@fontsource-variable`, OFL 1.1). The app makes no font requests to the network.
* Cmd/Ctrl+K command palette for pages, new chat / note / task and the theme.
* Chat: new message layout, code blocks with a language label and a Copy button, a streaming reveal with a caret, a
  typing indicator, an auto-growing composer with the model picker, and suggestion cards on an empty chat.
* Redesigned app shell and sidebar, auth, the desktop welcome flow and the desktop splash (`desktop/ui/index.html`), with
  skeleton loaders, empty states, toasts and modals.
* Motion stays between 120 and 240 ms and is CSS only. All of it switches off under `prefers-reduced-motion`. Focus
  rings are visible and every new control works from the keyboard.
* Bundle: JS 398.6 → 411.1 kB (gzip 118.7 → 122.7 kB), CSS 14.5 → 39.6 kB (gzip 3.8 → 8.6 kB), plus 88.7 kB of Latin
  fonts (Latin Extended, 85 kB, loads only when a page needs it). No new JS dependencies.
* Docs: README image size now uses the CI figure (248 MB), and the BACKLOG note for item 116 records that it is merged.

No API, schema or backend changes; no action needed on upgrade. Rollback: revert the merge.

## 2026-10-30 · Desktop app for macOS and Windows (preview) · branch feat/desktop-app

* New `desktop/`: a Tauri v2 app that runs Workbench natively, without Docker. The backend is frozen with PyInstaller
  and started as a sidecar on `127.0.0.1` with a per-user data folder; the window signs itself in with a one-time
  launch token (no password).
* First-run welcome: paste an API key for any key-based provider, or use local Ollama, then land in chat.
* Provider keys, `APP_SECRET` and the tools key live in the OS keychain in the desktop app.
* `.github/workflows/desktop.yml` builds `.dmg` (Apple silicon + Intel), `.msi` + `.exe`, `.deb` + `.AppImage` on manual
  dispatch and on tags. Signing, notarisation and the updater activate when their secrets exist
  ([docs/desktop-release.md](docs/desktop-release.md)). Builds are unsigned until then.
* MIT `LICENSE` added (owner to confirm).

Docker installs: no action needed and no schema change. The new backend behaviour only switches on with
`WORKBENCH_DESKTOP=1`, which only the desktop app sets; Docker keeps Fernet-encrypted keys under `APP_SECRET`.
Rollback: revert the merge; desktop-app data lives in its own per-user folder and does not touch Docker volumes.

## 2026-10-30 · Terminal coding agent (Rooks Code) · Phase 2

**New:** `rooks-code/`, a model-agnostic coding agent for the terminal (OpenAI, Anthropic, OpenRouter, any
OpenAI-compatible server, Ollama, LM Studio). Interactive and one-shot modes; read, search, diff-based edits and shell
commands in a loop; approval by default, trusted folders, `--dry-run`, undo of the last prompt's change set, hard
per-session token and dollar caps, a stdio MCP client, and receipts in Workbench's run-log format
(`rooks receipts import --db workbench.db`). Standard library only, Python 3.11+. CI tests it on macOS, Windows and Linux.

**Migration steps:** none. It is a separate, optional tool: install with
`uv tool install "git+https://github.com/Inclined88888/ai-workspace#subdirectory=rooks-code"`. Workbench, its database
and `cli/ws` are unchanged.

**Rollback steps:** `uv tool uninstall rooks-code` (or `pip uninstall rooks-code`). Per-project `.rooks/` folders hold
only receipts and undo snapshots and can be deleted.

## 2026-10-30 · Rooks Browser: a local AI browser agent (phase 3)

New, optional: `rooks-browser/`, a browser agent that drives your own Chrome or Edge through Playwright. It installs
as a ≈ 150 MB Python package, not a 2.5 GB image.

* **Models:** OpenAI, Anthropic, OpenRouter, Ollama, OpenAI-compatible servers or Workbench's model connections.
* **Pages:** it reads the page's element list and text, plus screenshots for vision models.
* **Actions:** search (SearXNG or a fallback search page), open, click, type, scroll, extract and download.
* **Approvals:** it asks before form submits, purchases, messages and logins.
* **Sites and receipts:** it keeps per-site allow and deny lists, and logs every visited URL as Workbench run-log
  receipts.
* **Passwords:** it never stores or types them.
* **Ways to call it:** a CLI, a local HTTP API that is compatible with the `browser` add-on's job API, and an MCP server.

Nothing changes for existing installs. The `browser` add-on, Workbench and the database are untouched.

* **To try it:** `uv tool install ./rooks-browser`.
* **To use it from Workbench:** set `BROWSER_URL=http://host.docker.internal:8765` and `BROWSER_API_KEY` to the token
  you start `rooks-browser serve --host 0.0.0.0` with.
* **Rollback:** remove those two settings, or uninstall the tool.

## 2026-10-29 · Fix upgrades from the original ROOKS database

Older ROOKS installations used `tool_runs(at, ok, actor, mission_id, ...)` before
receipt-style `created_at`/`outcome` fields existed. Upgrades now detect and migrate
both layouts instead of crash-looping with `no such column: created_at`. Original
timestamps, execution outcomes, latency, conversation links and actor/mission
metadata are preserved; historical success is not labelled verified. Sensitive
arguments/errors are redacted in the new trace. A full pre-upgrade database backup
is retained. Unknown layouts roll back without dropping the source rows.

Rebuild/restart Workbench after pulling this fix. No database reset, volume deletion,
secret rotation or manual SQL is needed. Existing schema-v2 installations are unchanged.

## 2026-10-29 · Phase 0 acceptance and release foundations · 73, 79, 83, 84, 80

* Authenticate main API schema discovery at `/api/openapi.json`.
* Terminal tasks carry persisted idempotency keys; `ws retry` deliberately reuses
  the key. Stream failures exit nonzero; config writes are atomic and private.
* Paperclip telemetry is explicitly disabled via upstream environment controls.
* Add keyless signed-artifact release workflow and verification instructions.
* Exercise task write/read/restart under a 1 GiB core-container limit in CI.
* Verify credential encryption through API writes and ciphertext readback tests.

No schema/key migration. Recreate enabled Paperclip containers to apply its new
environment. Roll back the image/client if needed; preserve volumes and APP_SECRET.
[Phase 0 gate](docs/PHASE0-GATE.md) records the acceptance assumptions and limits.
## 2026-10-29 · Official Codex CLI launcher · Phase 1 item 117

Optional `codex` profile reuses Item 116's unchanged Node transport. The official
CLI is pinned to 0.163.0; a CLI-specific adapter sanitizes login status and forwards
only the task result. Analytics, feedback and all OTel exporters are disabled.

Enable and authenticate through [the official CLI](cli-launcher/CODEX.md).
No database migration or core dependency change. Roll back by disabling the profile;
auth and workspace volumes remain. Live login/inference and egress checks require
owner acceptance.

## 2026-10-29 · Official Claude Code launcher · Phase 1 item 116

**New:** optional `claude-code` Compose profile, with the official CLI pinned to 2.1.308.
The dependency-free Node transport checks official CLI auth, sends tasks on stdin,
returns output privately, and terminates bounded subprocesses. Nonessential traffic
and auto-updates are disabled. No provider/session adapter or core Node dependency.

**Migration steps:** opt in with `./setup.sh --with claude-code` (include any existing
add-ons in the list), then sign in through the official CLI. See [launcher setup and
API](cli-launcher/README.md). No database migration or breaking change.

**Rollback steps:** disable with `./setup.sh --with none`, or rerun setup listing only
your other add-ons. Login and workspace volumes are kept; official logout and volume
removal are documented separately. Existing core installs stay off by default.

## 2026-10-29 · Outbound audit log · Phase 0 item 75

**New**
* Every outbound request Workbench makes is recorded in the run log as an `egress` row: destination host, local or
  internet, which part of Workbench made it, method and path, outcome and latency. Query strings are not recorded,
  and tokens in URL paths are masked.
* The System status page has an **Outbound connections** section. It shows each host contacted in the last 24 hours, and
  a "Local only" line when nothing went beyond this machine or its private network.
* API: `GET /api/egress?hours=24` (1 to 2160). It needs sign-in or the CLI token.

**Not covered:** traffic that add-on containers make themselves, for example pages the browser agent visits or models
the ollama container downloads. Workbench's own calls to them are recorded. See BACKLOG.

**Breaking changes:** none. The run log grows by one row per outbound request. **Migration steps:** none (schema v2
unchanged; design record 0001 documents the rows). **Rollback steps:** run the previous image. Existing `egress` rows
stay and are ignored.

## 2026-10-29 · System status page · Phase 0 item 25

**New**
* A **System status** page (sidebar, `/status`) shows each part as Working, Degraded, Down or Not set up, with the reason
  and the probe time. Parts covered:
  * the database (schema version, free disk) and the four background loops;
  * every model connection, probed for real: a rejected key or an unreachable server is Down, even when model names
    were typed in by hand;
  * the browser and SearXNG add-ons;
  * web search, Telegram, calendars and mail accounts.
* Checks run only when the page is opened or Check again is pressed, never in the background (R8). Results are kept for
  one minute. Paid search APIs (Brave, Tavily) are shown as configured but not probed, because a probe costs a query.
* API: `GET /api/health` (`fresh=true` to re-check). It needs sign-in or the CLI token. A probe also records `last_ok_at`
  and `last_error` on the connection.

**Changed**
* `/healthz` now also reads the database. It answers 503 when SQLite is unreadable, the schema version is wrong or the
  data volume has under 50 MB free, so Docker marks the container unhealthy instead of reporting it healthy.

**Breaking changes:** none. **Migration steps:** none (schema v2 unchanged). **Rollback steps:** run the previous image.

## 2026-10-29 · A change is made once · Phase 0 item 28

**New**
* When a model repeats a write in the same turn (the same tool with the same arguments, nothing else changed in between),
  the repeat is not run. The run log records it as `duplicate`, and the model is told it was already done. Reads and
  retries after a failure still run.
* `POST /api/assistant/chat` accepts an `Idempotency-Key` header. A retried request with the same key is not run again;
  the answer is 409 with the first turn's `conversation_id`, `turn_id` and `outcome`. A malformed key gets 400.
* Telegram no longer answers a message twice when the bot restarts before Telegram has been told the message was read.

**Breaking changes:** none. A client that sends an `Idempotency-Key` it reuses for different messages will now get 409;
send a new key per message. **Migration steps:** none (schema v2 unchanged; design record 0001 documents the keys).
**Rollback steps:** run the previous image. Rows recorded as `duplicate` stay in the run log and count as neither success
nor failure in the reliability record.

## 2026-10-29 · Run log page · Phase 0 item 21

**New**
* A **Run log** page (sidebar, `/runs`) lists every turn and every model call made outside a turn, newest first, with its
  outcome, channel, model, model and tool call counts, tokens, cost and duration. Filter by channel or show only runs
  with a failed step.
* Opening a run shows its trace: each model call (provider, auth kind, protocol, latency, tokens, cost), each tool call
  (effect, redacted arguments, failure, receipt) and each approval decision nested under the call it answered.
* API: `GET /api/runs` (`limit`, `before`, `channel`, `failed`, `conversation_id`) and `GET /api/runs/{id}`. Both need a
  signed-in session or the CLI token. Encrypted raw arguments of held calls are never returned.

**Breaking changes:** none. **Migration steps:** none; the page reads the existing run log (schema v2 unchanged).
**Rollback steps:** run the previous image. No data changes.

## 2026-10-29 · The browser agent uses Workbench's models · Phase 0 item 3 (part b)

**Breaking changes**
* `BROWSER_LLM_PROVIDER` and `BROWSER_LLM_MODEL` in `.env` are no longer used by the stack. The browser agent now calls the
  models connected in Workbench through Workbench (`/v1/chat/completions`, authenticated with `BROWSER_API_KEY`).
  * Pick the model on the Browser agent page.
  * From chat, the conversation's model is used.
  * Paperclip and Hermes browser runs use Workbench's default model.
* The browser container no longer receives `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY` or `OPENROUTER_API_KEY`.
  Inside the stack it refuses any other provider (rule R1).
* Browser model calls now appear in Workbench's usage (channel `browser`). Monthly totals rise by what browser runs already
  spent, which was previously invisible to Workbench.
* The standalone `browser-agent` CLI, used without Workbench, keeps its direct providers.

**Migration steps**
1. Connect the model you want the browser to use in Settings → Model providers. A previous browser-only key must be
   added there.
2. Run `./setup.sh` or `docker compose up -d` to recreate the browser container.

**Rollback steps:** run the previous images. Their compose file passes the provider keys to the browser container again.

## 2026-10-29 · Schema version 2: connections (auth kind + protocol) · Phase 0 item 3

**Breaking changes**
* The `providers` table is replaced by `connections`. Each connection has an `auth_kind` (API key, local Ollama, OpenAI
  compatible server, subscription sign-in) and a `protocol` (Chat Completions, OpenAI Responses, Anthropic Messages, Ollama
  native) instead of the old `kind`. Ids are kept, so every saved model choice (`3/claude-…`) keeps working.
* `/api/providers` returns `auth_kind`, `protocol`, `state` and `last_error` instead of `kind`. It still accepts `kind` when
  creating a connection.
* New connections from the **OpenAI** preset use the Responses API, and from the **Anthropic** preset the native Messages API.
  Existing connections keep the protocol they used (Chat Completions); change it in Settings → Model providers.
* Connection types that are not legitimate are refused with an R5 message. No existing connection is affected: none can be of
  that type.
* A connection that cannot be used (for example one that needs sign-in) no longer breaks the model list; it shows its error in
  Settings.

**Migration steps:** automatic on first start. Workbench copies the database to `workbench.db.pre-v2-<UTC time>.bak`, then moves
every provider into `connections` in one transaction. On failure nothing changes and the database stays at version 1.

**Rollback steps**
1. `docker compose stop workbench`
2. Replace `workbench.db` with the newest `workbench.db.pre-v2-*.bak` (delete `workbench.db-wal` and `workbench.db-shm` if present).
3. Start the previous image.
4. Connections added after the upgrade are lost.

## 2026-10-29 · Logs and error messages are redacted (R7, item 110) · PR #4

**Breaking changes**
* Logs, the run log's text columns, HTTP error bodies (including 422 validation errors) and chat error events no longer
  contain the following, each replaced by a marker such as `<redacted>`, `<redacted:key>`, `<email>` or `<phone>`:
  * Telegram bot tokens
  * cookies and session ids
  * Authorization header values
  * JWTs
  * provider API keys (`sk-…`, `sk-ant-…`, `sk-or-v1-…`, `AIza…`, `gh*_…`, `xox*-…`, `tvly-…`, Workbench `wb_…` terminal tokens)
  * named secrets (`api_key`, `access_token`, `refresh_token`, `id_token`, `client_secret`, `password`, …)
  * email addresses
  * phone numbers
* Anything that parsed Workbench logs for those values (for example an email address in an approval or receipt trace)
  now sees the marker.
* Conversations, notes, mail and other user data are not changed.

**Migration steps:** none. It applies from the first start.

**Rollback steps:** run the previous image. Log lines written while this version ran stay redacted.

**Known limit:** a phone number written as one unbroken run of digits (`0412345678`) is not redacted. It cannot be told
apart from timestamps and ids.

## 2026-10-29 · Schema version 1: one run log (`actions`) · PR #3, merge 36b8070

**Breaking changes**
* The tables `usage`, `tool_runs`, `turn_checks` and `pending_actions` are replaced by one table, `actions`. External
  scripts that queried the old tables must query `actions` (see docs/design/0001-run-log-and-connections.md for the
  column mapping).
* Approval request ids change during the upgrade. The web app refetches them. An id copied before the upgrade and typed
  into `ws approve <id>` returns 404: run `ws approvals` again.
* **Cost reports split for one billing period.** Legacy `usage` rows keep the bare model name (`gpt-x`) in `model`.
  New rows store the model reference (`3/gpt-x`). For the month of the upgrade, Settings → Usage lists the same model
  twice, once per form, and the totals per model are split across the two lines. The monthly total and the per-agent
  budgets are correct, because they sum all rows. From the next month the split disappears.
* Model calls made by research, writing assist, compare and task breakdown are now recorded and counted. Monthly
  totals can rise compared with before, because that spend was previously invisible.

**Migration steps:** automatic on first start.
1. If any legacy table exists, Workbench copies the database file to `workbench.db.pre-v1-<UTC time>.bak` next to it
   (in the `workbench` volume).
2. All legacy rows are copied into `actions` in one transaction, the legacy tables are dropped, and
   `PRAGMA user_version` is set to 1.
3. If anything fails, the transaction rolls back, the database stays at version 0 with all data, and the error is
   logged.

**Rollback steps**
1. `docker compose stop workbench`
2. In the `workbench` volume, replace `workbench.db` with the newest `workbench.db.pre-v1-*.bak` (and delete
   `workbench.db-wal` and `workbench.db-shm` if present).
3. Start the previous image.
4. Anything recorded after the upgrade (costs, approvals, tool runs) is lost.

