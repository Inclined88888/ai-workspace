# Data model and memory (feat/memory-core)

Python package: `workbench/backend/app/knowledge/` (`API_VERSION = 1`). HTTP: `/api/knowledge/*`.

## 1. Audit: what existed before (v0.2)

| Store | Where | Versioning | Notes |
|---|---|---|---|
| Core tables (settings, connections, docs+FTS, tasks, calendars, events, mail_*, linked_accounts, conversations, messages+FTS, memories+FTS, skills, hub_skills, schedules, inbox, agents, agent_tasks, devices, actions...) | `db.py` SCHEMA | `PRAGMA user_version` = 2 | `ADDED_COLUMNS` (#26 mail/calendar `kind`, `linked_account_id`) added in place **without** a version bump |
| Legacy `memories` (plaintext) | `db.py` | – | migrated into `adapt_facts` by #29; table kept empty |
| "What I know about you" `adapt_facts`, recordings, playbooks, runs, activity, suggestions, lessons, routines | `adapt/store.py` (#29) | none (`ensure()` on first use) | encrypted with workspace Fernet key (APP_SECRET); BM25 in Python memory |
| Projects `projects`, `project_items`, `project_dismissed` | `projects.py` (#36) | none | people/notes are encrypted rows of `project_items`; `tasks.project` is a free-text name, not a key |
| History `history_entries` | `history.py` (#36) | none | |
| Automations / jobs `automations`, `jobs`, `job_notices` | #34 | none | |
| Files `work_files`, Sites `sites`, `site_records`, `site_checks`, `site_watches`, `site_actions` | #30 | none | |
| Meetings, checked answers, packs (trade/legal/study) | #35–#37 | none | |
| Account / plan | `settings` keys (#32) | – | |
| Appearance | `~/.ai-workspace/appearance.json` | – | outside the DB |
| CLI runs | `DATA_DIR/cli-runs/*.json` | – | outside the DB |

Problems: three ways of creating tables, no version for 30+ tables, people exist in four places (project_items, adapt_facts text, site_records.person, mail) with no shared id, `tasks.project` is a string, three separate search mechanisms, no change log.

## 2. Schema versioning

`knowledge/schema.py` adds `schema_migrations(version, name, checksum, applied_at)` and numbered steps, alongside `PRAGMA user_version` (still owned by `db.migrate` for core tables):

1. adopt every feature module's tables (imports their SCHEMA strings) + `db.ensure_columns`
2. knowledge tables (below) + change-log triggers
3. backfill the graph and memories from existing stores (adapters)

Each step: one transaction, backup file `workbench.db.pre-kn<N>-<ts>.bak` before the first pending step, never re-run. `schema.integrity()` = `PRAGMA integrity_check` + foreign keys + index orphan checks. To add a step: append `(4, "name", fn)` to `MIGRATIONS`; make it idempotent.

## 3. Tables

- `kn_entities` – types: person, organisation, project, task, document, site, event, thread, place, asset, note, user. `uid` (sync id), `name`, `name_key`, `attrs_enc` (encrypted JSON), `ref_table/ref_id` (pointer to the source store row; unique), `scope`, `privacy`, `pinned`, `source`.
- `kn_aliases` – blind tokens of emails, phones, short names → entity (dedupe and matching).
- `kn_edges` – `src rel dst`: works_at, member_of, assigned_to, about, mentions, derived_from, part_of, attended, located_at, owns.
- `kn_memories` – tiers profile | episodic | project | procedural; `content_enc`, `subject_id` (entity; NULL = the user), `predicate` (for contradictions), `project_id`, `scope`, `privacy`, `pinned`, `confidence`, `status` active|superseded|conflict|archived, `superseded_by`, `consolidated_into`, provenance (`source_kind`, `source_ref`, `evidence_enc`, `observed_at`), usage (`uses`, `last_used_at`, `helpful`, `weight`).
- `kn_docs` + `kn_fts` (FTS5) + `kn_vectors` – one search index over everything; FTS holds **keyed tokens** only.
- `kn_conflicts`, `kn_filings`, `kn_changes` (sync log, filled by triggers), `kn_improvements` (nightly pass; table only so far).

## 4. Scopes and privacy

`scope`: `shared` (every assistant), `private`, `assistant:<id>` (a teammate), `team:<id>`. `knowledge.scoped("assistant:3")` sees its own scope + shared. `privacy`: normal | private | sensitive (sensitive is never sent to cloud models — enforcement point for callers; not wired yet).

## 5. Rules

- Dedupe: same scope/tier/subject and ≥75% word overlap → refresh the existing memory.
- Contradictions (same subject + predicate): about another person/thing → newer wins, old kept as history (`superseded`); about the user, or when the old one came from the user → both marked `conflict` and a review card (`/api/knowledge/conflicts`, resolve old|new|both). The user's own word beats an automatic guess.
- Secrets (passwords, card numbers, tokens) are refused unless typed by the user → belong in the Vault.
- Consolidation: `consolidate(days)` turns old episodic notes per project into one durable memory; episodes archived with `consolidated_into` so "why" still shows them.
- Why: `why(id)` → plain sentence + source, date, confidence, use count, what it replaced.

## 6. Recall

`recall(query, scopes, project_id, k)` = reciprocal-rank fusion of FTS5 BM25 (blind tokens) + vectors (optional) + graph expansion (entities named in the query and their neighbours) × tier recency half-life × confidence × nightly weight × current-project boost × pin boost.
`context_for(query, budget_tokens)` = budgeter: pinned facts (≤⅓ budget), then recalled memories, skipping near-duplicates.
Embeddings: plug point `set_embedder(obj with name, dim, embed(text))`. Built-in `HashEmbedder` (offline, not semantic). fastembed not bundled (model download; not verified on Mac/Windows here).

Benchmark (tests/test_knowledge.py, 10 cross-field facts + 30 noise items): FTS-only precision@1 1.00, recall@5 1.00; hybrid(hash) precision@1 1.00, recall@5 1.00. Small synthetic set – a ceiling check, not a real-world number.

## 7. Encryption

`knowledge/crypto.py`: random data key in the OS keychain (desktop, `WORKBENCH_SECRET_BACKEND=keychain`), else derived from APP_SECRET. Choice + check value stored once in settings so keys never switch silently. Plain on disk by design: entity names/types, doc titles of files/notes/events (needed for listing), timestamps.

## 8. For other branches (adoption)

- feat/grok-style: `teammates/memory.py` `_backend()` looks for `knowledge.scoped` – it exists. Map `add/notes/delete/recall` to `knowledge.scoped(f"assistant:{tid}")`; recall returns dicts (`content`, `id`, `score`).
- feat/team-lead: staff = `upsert_entity("person", name, {"email","phone","role"}, ref=("team_members", id))`; tasks = `upsert_entity("task", title, {...}, ref=("team_tasks", id))`; `link(task, "assigned_to", person)`, `link(task, "part_of", project)`. Put status notes in `remember(..., "episodic", subject_id=person, project_id=project, scope="team:<id>")`.
- Projects: project entity id = `adapters.project_entity(projects.id)`.
- Anything searchable: `index_item(kind, ref, text, title, scope, privacy, project_id)` / `unindex_item`.
