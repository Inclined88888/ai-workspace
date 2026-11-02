# Teammates, groups, Vault, drafts, Pages, channels and the phone link (feat/grok-style)

Backend: `workbench/backend/app/teammates/`. Shared files get one tagged hook line each (`# feat/grok-style`):
`main.py` (routers + Slack, Discord and phone-relay loops), `assistant.system_prompt`, `assistant.available_tools`, `telegram._conversation`.

| Area | API | Notes |
|---|---|---|
| Teammates | `GET/POST /api/teammates`, `GET/PATCH/DELETE /api/teammates/{id}`, `POST /api/teammates/{id}/chat` (SSE) | Templates: personal, work, study, trade, research, legal. Persona, avatar, colour, sections, tool groups, model. The default teammate can't be deleted. |
| Memory | `POST/DELETE /api/teammates/{id}/notes` | `memory.py` is a thin adapter; it switches to `app.knowledge.scoped` when feat/memory-core lands. Scope `own` or `shared`. |
| Routines | `POST /api/teammates/{id}/routines` | Plain words, then `automations.parse/create` (ask first), mapped to the teammate. Job conversations resolve to the owner teammate. |
| Computer | `POST /api/teammates/{id}/computer/sign-in` | Own working folder `DATA_DIR/teammates/<id>/work` and Rooks Browser profile `tm-<id>`. |
| Templates | `GET /api/teammates/{id}/template`, `POST /api/teammates/import` | JSON `ai-workspace.teammate` v1. No notes or secrets; routines come in as words for review. |
| Groups | `POST /api/groups`, `GET/PATCH/DELETE /api/groups/{id}`, `POST /api/groups/{id}/messages`, `POST /api/reactions` | @mention routing, @handoffs (max 4 hops). Nothing that sends or acts outside runs in a group. |
| Vault | `/api/vault`, `/api/vault/forms/{id}` | Keychain when enabled, otherwise Fernet. Masked hints only; the model never sees secrets. Luhn check on cards. |
| Drafts | `/api/outbox`, `POST /api/outbox/{id}/send` | `draft_message` tool. Sending happens only from web, desktop or mobile (user press), claimed atomically, and never twice. |
| Pages | `/api/pages`, `/api/pages/{id}/export.html`, public `/p/{token}` | Safe Markdown, self-contained HTML with CSP and no scripts. Sunshine is the default palette. |
| Channels | `GET /api/channels`, `PUT /api/channels/slack`, `PUT /api/channels/discord` (+ `/test`), `PUT /api/channels/telegram` | Slack Socket Mode with a manifest and steps; Discord Gateway (DMs and @mentions only, no privileged intents, invite link generated after Test); allowlist; idempotent per message. Inbound messages never send: anything outbound waits for approval in the app. |
| More channels | `PUT /api/channels/matrix`, `/signal` (+ `/test`), `/whatsapp`, `/email`; public `GET/POST /hooks/whatsapp` | `more_channels.py`. Matrix /sync long poll (joins only on an allowed user's invite; no E2EE rooms). Signal via signal-cli-rest-api (native/normal mode) /v1/receive and /v2/send, Note to Self works. WhatsApp Cloud API webhook: verify token on GET, X-Hub-Signature-256 checked on POST. Email: subject tag + sender allowlist on the connected mail account, only mail after it is turned on, a reply per message. All allowlisted and idempotent per message. |
| Phone link | `/api/link`, `/api/mobile/link/keys`, `/api/mobile/link/sealed`, `/api/mobile/files/*`, handoffs | X25519 + HKDF-SHA256 + ChaCha20-Poly1305. The device token travels inside the ciphertext. Replay-protected, `/api/mobile/*` only. LAN first, relay (`link_relay_url`, https only) as fallback. |
