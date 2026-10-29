# 0001: Run log and connection schemas

Status: run log **built** (Phase 0 item 1). Connection table **designed**, to be built in Phase 0 item 3 (R1 split).

## Run log: `actions` (R2)

One table, one writer module (`app/actions.py`). It replaces `usage`, `tool_runs`, `turn_checks` and `pending_actions`.

| Column | Meaning |
| --- | --- |
| `id`, `ts` | row id, UTC ISO time |
| `kind` | `turn`, `model_call`, `tool_call`, `decision` |
| `agent_id`, `channel`, `conversation_id` | who and where ("agent" in the spec is `agent_id`; NULL is the assistant itself) |
| `provider`, `model`, `auth_kind`, `protocol` | which backend answered; `model` is the model ref |
| `tool`, `effect`, `args`, `payload_enc` | tool calls. `args` is redacted for display and trace. `payload_enc` holds encrypted raw arguments, kept only for held calls that may run later. |
| `latency_ms`, `tokens_in`, `tokens_out`, `usd_cost` | `usd_cost` NULL means the price is unknown |
| `outcome`, `failure_reason`, `verified`, `evidence`, `detail`, `from_text` | result, receipts, flags |
| `request_id` | unique idempotency key (uuid4 hex); item 28 will give it caller-supplied semantics |
| `parent_action_id` | tree: turn → model and tool calls; held call → decision → executed call |

Rules:
* `llm.py` records every model call itself (chat, complete, stream), success or failure. Before this change, research, writing assist, compare and task breakdown spent tokens with no row.
* A turn's row is opened when the turn starts and always closed: with a result, `error`, `interrupted` (stream closed), or `interrupted` on restart (`actions.recover`).
* An approval is a `decision` row whose parent is the held call. A partial unique index allows one decision per parent, which replaces the old status-update race guard.
* Text columns go through `redact.redact` on write (R7). Raw arguments needed later are encrypted, never stored in plain text.

### Migration (schema version 0 → 1)
* `PRAGMA user_version` now versions the schema.
* On the first start with legacy tables present, the database file is backed up to `workbench.db.pre-v1-<UTC>.bak` with SQLite's backup API.
* Every legacy row is then copied into `actions` in one transaction, the legacy tables are dropped, and the version is set to 1.
* A failure rolls the whole transaction back: legacy data untouched, version stays 0 (tested).
* Verified by upgrading a real `main` container volume.

Mapping:
* `usage`: one `model_call` per row. `priced = 0` becomes `usd_cost` NULL. `model` stays the bare model name for old rows.
* `tool_runs`: one `tool_call`; `ms` becomes `latency_ms`.
* `turn_checks`: one `turn`; `detail` becomes `failure_reason`.
* `pending_actions`:
  * Pending: a `tool_call` with outcome `held`.
  * Refused in an unattended run (`denied` with no `decided_at`): a `tool_call` with outcome `denied`.
  * Answered: the held `tool_call` plus a `decision` row.
  * Executed (`done` or `failed`): additionally a child `tool_call` with outcome `ok` or `tool_error` and the stored result.
  * Approval ids change, because rows get new ids. The UI refetches; an old id typed into the CLI returns 404.

### Rollback
Stop Workbench, move the `.bak` file over `workbench.db`, and start the previous image. Anything recorded after the upgrade is lost. That is the cost of a one-way schema change, and why the backup is automatic.

## Connections (R1), for Phase 0 item 3

It replaces `providers`. Two separate fields replace today's overloaded `kind`.

```sql
CREATE TABLE connections (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  auth_kind TEXT NOT NULL,        -- api_key | oauth_subscription | experimental_session_slot | local_ollama | openai_compatible
  protocol TEXT NOT NULL,         -- responses | chat_completions | anthropic_messages | ollama_native
  base_url TEXT NOT NULL,
  secret_enc TEXT NOT NULL DEFAULT '',   -- API key, or OAuth refresh token (encrypted)
  token_expires_at TEXT,                 -- OAuth access token expiry
  state TEXT NOT NULL DEFAULT 'connected', -- connected | needs_auth | refreshing | disabled (item 108)
  scopes TEXT NOT NULL DEFAULT '',       -- granted OAuth scopes, e.g. the separate plan-usage scope
  provider_cap TEXT NOT NULL DEFAULT '', -- the provider's own cap as reported or entered (item 104); never invented here
  residency TEXT NOT NULL DEFAULT 'cloud', -- local | cloud (item 109)
  models TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_ok_at TEXT, last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
```

Decision on `experimental_session_slot` (product ambiguity, reversible):
* R1 lists it as one of the five backends, but R5 and D3 forbid session scraping.
* It stays as a reserved enum value. Its adapter always refuses with the R5 reason, and its contract test asserts that refusal.
* Alternative considered: deleting the value from the enum. Rejected, because R1 names five backends.
* Reversibility: dropping the value later needs only a CHECK change, with no data, since nothing can create such a connection.
