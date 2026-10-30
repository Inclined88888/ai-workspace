# API and terminal client

`GET /api/openapi.json` returns the main API's OpenAPI 3 schema to an authenticated
browser session or terminal bearer token. It is not cached and contains no stored
credential values. API descriptions include their actual session/terminal/bridge
security schemes. `/tools/openapi.json` describes the separately mounted tool API;
`/v1` remains the browser bridge. These keys have different scopes.

State-changing `/api` calls also require `X-Workbench: 1` (CSRF protection). No CORS
permission is granted. A terminal token does not authorize session-only routes such
as provider administration. Obtain/revoke terminal tokens in Assistant > Settings;
the server stores only their SHA-256 hashes and rotation revokes the previous token.

Copy executable `cli/ws` to your PATH; Python 3.10+ standard library is sufficient.
`ws --help` works before login. `ws login http://localhost:3100` prompts privately
for a terminal token. HTTPS or a private tunnel is needed across untrusted networks.
`ws logout` deletes local credentials; rotate the server token to revoke remote copies.

Chat requests share the web/Telegram runtime. Each new CLI task gets a random
Idempotency-Key, saved before sending in the local mode-0600 config file. `ws retry`
reuses the original key and body after interruption. A 409 means the server already
accepted it; the turn is not run again. A new task intentionally gets a new key.
Stream errors and incomplete streams return a nonzero exit status. Completed turns
clear the local pending request. There are no automatic retries of side effects.

The local config includes the terminal token, last conversation and any pending
prompt: keep it private. Atomic writes create mode-0600 files before writing data.
Server-side credentials are encrypted, but this client config and the deployment
`.env` are protected by file permissions, not encryption.

## Phone companion app

The iPhone and Android app pairs with a one-time code from Settings → Phone and then uses a device token that reaches
only `/api/mobile/*` (approvals, runs, chat, tasks, notes, attachments, push token). See `docs/mobile-pairing.md` for
every route, the LAN listener, the relay interface and push setup.
