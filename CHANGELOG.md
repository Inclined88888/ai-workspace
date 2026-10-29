# Changelog

Every user-visible change that needs action, or changes data on upgrade, gets an entry: date, version, breaking changes,
migration steps, rollback steps. Workbench has no release numbers yet (BACKLOG). Entries are identified by schema
version and the merge commit on `main`.

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
