# Changelog

Every user-visible change that needs action, or changes data on upgrade, gets an entry: date, version, breaking changes,
migration steps, rollback steps. Workbench has no release numbers yet (BACKLOG). Entries are identified by schema
version and the merge commit on `main`.

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
