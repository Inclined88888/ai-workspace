"""Schema versioning for the whole workspace database (feat/memory-core).

Before this, tables were created three ways: db.SCHEMA with PRAGMA user_version (core, version 2), `ensure()` in each
feature module (projects, history, adapt, jobs, automations, sites, files, meetings, checked answers, packs) with no
version at all, and columns added in place (db.ADDED_COLUMNS, from #26) without a version bump. See docs/data-model.md.

This runner adds one ledger, `schema_migrations(version, name, checksum, applied_at)`, and numbered steps. It leaves
PRAGMA user_version to db.migrate (core tables), so both can run in any order. Each step runs in one transaction,
a file backup is taken before the first pending step on a database that already has data, and a step is never
re-run. Steps must be idempotent on databases that already have some of the tables (old installs created them lazily).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import config, db

log = logging.getLogger("knowledge.schema")

KNOWLEDGE_SQL = """
-- Things the workspace knows about: people, organisations, projects, tasks, files, sites, events, threads, places,
-- assets, notes. Rows that mirror an existing store keep (ref_table, ref_id); the store stays the source of truth.
CREATE TABLE IF NOT EXISTS kn_entities (
  id INTEGER PRIMARY KEY,
  uid TEXT NOT NULL UNIQUE,                    -- stable id for sync and export
  type TEXT NOT NULL,                          -- person | organisation | project | task | document | site | event | thread | place | asset | note | user
  name TEXT NOT NULL DEFAULT '',               -- display name (not secret: names are needed to list and match)
  name_key TEXT NOT NULL DEFAULT '',           -- normalised name for de-duplication
  attrs_enc TEXT NOT NULL DEFAULT '',          -- encrypted JSON: email, phone, role, notes, anything else
  ref_table TEXT NOT NULL DEFAULT '',          -- projects | tasks | work_files | sites | events | conversations | docs | ...
  ref_id TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT 'shared',        -- shared | assistant:<id> | team:<id> | private
  privacy TEXT NOT NULL DEFAULT 'normal',      -- normal | private | sensitive
  pinned INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'auto',         -- auto | user | import:<kind> | adapter
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_seen_at TEXT,
  deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS kn_entities_ref ON kn_entities(ref_table, ref_id) WHERE ref_table != '';
CREATE INDEX IF NOT EXISTS kn_entities_type ON kn_entities(type, name_key);
CREATE TABLE IF NOT EXISTS kn_aliases (
  entity_id INTEGER NOT NULL REFERENCES kn_entities(id) ON DELETE CASCADE,
  alias_key TEXT NOT NULL,                     -- blind token of a normalised alias (name, email, phone, short name)
  PRIMARY KEY (entity_id, alias_key)
);
CREATE INDEX IF NOT EXISTS kn_aliases_key ON kn_aliases(alias_key);
CREATE TABLE IF NOT EXISTS kn_edges (
  id INTEGER PRIMARY KEY,
  uid TEXT NOT NULL UNIQUE,
  src INTEGER NOT NULL REFERENCES kn_entities(id) ON DELETE CASCADE,
  rel TEXT NOT NULL,                           -- works_at | member_of | assigned_to | about | mentions | derived_from | part_of | attended | located_at | owns
  dst INTEGER NOT NULL REFERENCES kn_entities(id) ON DELETE CASCADE,
  confidence REAL NOT NULL DEFAULT 1.0,
  source TEXT NOT NULL DEFAULT 'auto',
  created_at TEXT NOT NULL,
  UNIQUE (src, rel, dst)
);
CREATE INDEX IF NOT EXISTS kn_edges_dst ON kn_edges(dst, rel);

-- Memories: what the assistant knows, with where it came from.
CREATE TABLE IF NOT EXISTS kn_memories (
  id INTEGER PRIMARY KEY,
  uid TEXT NOT NULL UNIQUE,
  tier TEXT NOT NULL,                          -- profile | episodic | project | procedural
  content_enc TEXT NOT NULL,
  fingerprint TEXT NOT NULL DEFAULT '',        -- blind tokens of the normalised words, for de-duplication
  subject_id INTEGER REFERENCES kn_entities(id) ON DELETE CASCADE,   -- who/what it is about (NULL = the user)
  predicate TEXT NOT NULL DEFAULT '',          -- lives_in | works_at | phone | email | role | prefers:<topic> | ...: for contradictions
  project_id INTEGER REFERENCES kn_entities(id) ON DELETE SET NULL,  -- the Project entity it belongs to
  scope TEXT NOT NULL DEFAULT 'shared',
  privacy TEXT NOT NULL DEFAULT 'normal',
  pinned INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0.7,
  status TEXT NOT NULL DEFAULT 'active',       -- active | superseded | conflict | archived
  superseded_by INTEGER REFERENCES kn_memories(id) ON DELETE SET NULL,
  consolidated_into INTEGER REFERENCES kn_memories(id) ON DELETE SET NULL,
  source_kind TEXT NOT NULL DEFAULT 'user',    -- user | chat | file | email | site | task | teach | feedback | import | consolidation | adapter
  source_ref TEXT NOT NULL DEFAULT '',         -- conversation:12 | file:4 | adapt_fact:9 | import:chatgpt
  evidence_enc TEXT NOT NULL DEFAULT '',       -- short quote it was learned from
  observed_at TEXT NOT NULL,                   -- when it was true / said
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT,
  uses INTEGER NOT NULL DEFAULT 0,
  helpful INTEGER NOT NULL DEFAULT 0,          -- net votes from use (used in a reply that was kept / thumbs)
  weight REAL NOT NULL DEFAULT 1.0             -- re-ranked by the nightly improvement pass
);
CREATE INDEX IF NOT EXISTS kn_memories_status ON kn_memories(status, tier);
CREATE INDEX IF NOT EXISTS kn_memories_subject ON kn_memories(subject_id, predicate);
CREATE INDEX IF NOT EXISTS kn_memories_project ON kn_memories(project_id);

-- One search index over everything (memories, entities, chats, notes, tasks, files, sites, events, mail drafts,
-- meetings). Tokens are keyed (crypto.blind) so the index holds no readable words.
CREATE TABLE IF NOT EXISTS kn_docs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                          -- memory | entity | message | note | task | file | site_record | event | meeting | draft | playbook
  ref TEXT NOT NULL,                           -- the row it points at
  title TEXT NOT NULL DEFAULT '',              -- short display title (names, not bodies)
  scope TEXT NOT NULL DEFAULT 'shared',
  privacy TEXT NOT NULL DEFAULT 'normal',
  project_id INTEGER,
  at TEXT NOT NULL,
  UNIQUE (kind, ref)
);
CREATE VIRTUAL TABLE IF NOT EXISTS kn_fts USING fts5(tokens, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS kn_vectors (
  doc_id INTEGER PRIMARY KEY REFERENCES kn_docs(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  vec BLOB NOT NULL
);

-- Conflicts the user decides on (simple review card)
CREATE TABLE IF NOT EXISTS kn_conflicts (
  id INTEGER PRIMARY KEY,
  old_id INTEGER NOT NULL REFERENCES kn_memories(id) ON DELETE CASCADE,
  new_id INTEGER NOT NULL REFERENCES kn_memories(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'open',         -- open | kept_old | kept_new | kept_both
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

-- Auto-filing: "Filed under X, change?"
CREATE TABLE IF NOT EXISTS kn_filings (
  id INTEGER PRIMARY KEY,
  item_kind TEXT NOT NULL,                     -- file | email | chat | task | note | event | site_record | meeting
  item_ref TEXT NOT NULL,
  target_id INTEGER NOT NULL REFERENCES kn_entities(id) ON DELETE CASCADE,
  reason TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL DEFAULT 'filed',        -- filed | confirmed | changed | undone
  created_at TEXT NOT NULL,
  UNIQUE (item_kind, item_ref, target_id)
);

-- Sync-ready change log: every write to kn_* is one row (filled by triggers below)
CREATE TABLE IF NOT EXISTS kn_changes (
  seq INTEGER PRIMARY KEY,
  tbl TEXT NOT NULL,
  uid TEXT NOT NULL,
  op TEXT NOT NULL,                            -- upsert | delete
  at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  device TEXT NOT NULL DEFAULT 'this-computer'
);

-- Nightly improvement pass: what it looked at, what it changed, what it proposes
CREATE TABLE IF NOT EXISTS kn_improvements (
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL,                           -- local YYYY-MM-DD it reviewed
  kind TEXT NOT NULL,                          -- note | applied | proposal
  title TEXT NOT NULL,
  detail_enc TEXT NOT NULL DEFAULT '',
  change TEXT NOT NULL DEFAULT '{}',           -- JSON describing a proposal (applied only after approval)
  status TEXT NOT NULL DEFAULT 'done',         -- done | proposed | approved | declined
  created_at TEXT NOT NULL
);
"""

TRIGGERS_SQL = "".join(
    f"""
CREATE TRIGGER IF NOT EXISTS {t}_log_ins AFTER INSERT ON {t} BEGIN INSERT INTO kn_changes(tbl, uid, op) VALUES ('{t}', new.uid, 'upsert'); END;
CREATE TRIGGER IF NOT EXISTS {t}_log_upd AFTER UPDATE ON {t} BEGIN INSERT INTO kn_changes(tbl, uid, op) VALUES ('{t}', new.uid, 'upsert'); END;
CREATE TRIGGER IF NOT EXISTS {t}_log_del AFTER DELETE ON {t} BEGIN INSERT INTO kn_changes(tbl, uid, op) VALUES ('{t}', old.uid, 'delete'); END;
""" for t in ("kn_entities", "kn_edges", "kn_memories"))


def _feature_schemas() -> list[tuple[str, str]]:
    """Every feature module's own CREATE TABLE text, imported (not copied) so this stays in step with them."""
    from .. import analysis, automations, checked, history, jobs, meetings, projects, sites
    from ..adapt import store as adapt_store
    from ..packs import store as packs_store

    return [("projects", projects.SCHEMA), ("history", history.SCHEMA), ("adapt", adapt_store.SCHEMA), ("jobs", jobs.SCHEMA),
            ("automations", automations.SCHEMA), ("sites", sites.SCHEMA), ("files", analysis.SCHEMA),
            ("meetings", meetings.SCHEMA_SQL), ("checked", checked.SCHEMA), ("packs", packs_store.SCHEMA)]


def _m1_adopt(c: sqlite3.Connection) -> None:
    """Version 1: adopt the tables v0.1/v0.2 created lazily, so every install has the same set from here on."""
    for _, sql in _feature_schemas():
        _exec_many(c, sql)
    from .. import db as core

    core.ensure_columns(c)


def _m2_knowledge(c: sqlite3.Connection) -> None:
    _exec_many(c, KNOWLEDGE_SQL)
    _exec_many(c, TRIGGERS_SQL)


def _m3_backfill(c: sqlite3.Connection) -> None:
    """Version 3: index what already exists (projects, people on projects, tasks, files, sites, events, notes, the
    "What I know about you" facts) into the entity graph and memory tiers. Existing tables are not changed."""
    from . import adapters

    adapters.backfill(c)


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "adopt feature tables (projects, history, adapt, jobs, automations, sites, files, meetings, checked, packs)", _m1_adopt),
    (2, "knowledge graph, memories, search index, conflicts, filings, change log, improvements", _m2_knowledge),
    (3, "backfill the graph and memories from existing stores", _m3_backfill),
]
LATEST = MIGRATIONS[-1][0]


def _exec_many(c: sqlite3.Connection, script: str) -> None:
    """executescript() commits; this runs each statement inside the caller's transaction instead."""
    buf = ""
    for line in script.splitlines(keepends=True):
        if line.strip().startswith("--") and not buf.strip():
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            if stmt and stmt != ";":
                c.execute(stmt)
            buf = ""
    if buf.strip():
        c.execute(buf)


def _checksum(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:12]


_ready: set[str] = set()
_running: set[str] = set()
_lock = threading.Lock()


def applied(c: sqlite3.Connection | None = None) -> list[dict]:
    c = c or db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
              "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
    return [dict(zip(("version", "name", "checksum", "applied_at"), r))
            for r in c.execute("SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version")]


def migrate(c: sqlite3.Connection | None = None, path: Path | str | None = None, upto: int | None = None) -> list[int]:
    """Apply pending steps. Returns the versions applied now."""
    c = c or db.conn()
    path = path or config.DB_PATH
    done = {m["version"] for m in applied(c)}
    pending = [m for m in MIGRATIONS if m[0] not in done and (upto is None or m[0] <= upto)]
    if not pending:
        return []
    has_data = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('conversations','projects','adapt_facts')").fetchone()[0]
    if has_data and str(path) != ":memory:" and Path(path).exists():
        backup = Path(f"{path}.pre-kn{pending[-1][0]}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.bak")
        if not backup.exists():
            with sqlite3.connect(backup) as dest:
                c.backup(dest)
    out = []
    for version, name, step in pending:
        c.execute("BEGIN IMMEDIATE")
        try:
            step(c)
            c.execute("INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                      (version, name, _checksum(name), db.now()))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            log.exception("knowledge migration %s failed; database left at the previous version", version)
            raise
        out.append(version)
    return out


def ensure() -> None:
    """Called by every knowledge entry point; cheap after the first call per database file."""
    key = str(config.DB_PATH)
    if key in _ready:
        return
    if key in _running:
        return  # a migration step (backfill) is calling back into the API
    with _lock:
        if key not in _ready:
            _running.add(key)
            try:
                migrate()
            finally:
                _running.discard(key)
            _ready.add(key)


def reset_ready() -> None:
    _ready.clear()


def integrity() -> dict:
    """PRAGMA integrity_check + foreign keys + orphan checks across kn_*; used by tests and the Data safety page."""
    c = db.conn()
    ensure()
    ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    fk = c.execute("PRAGMA foreign_key_check").fetchall()
    orphans = {
        "memories_without_subject": c.execute("SELECT COUNT(*) FROM kn_memories m WHERE subject_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM kn_entities e WHERE e.id = m.subject_id)").fetchone()[0],
        "fts_without_doc": c.execute("SELECT COUNT(*) FROM kn_fts f WHERE NOT EXISTS (SELECT 1 FROM kn_docs d WHERE d.id = f.rowid)").fetchone()[0],
        "docs_without_fts": c.execute("SELECT COUNT(*) FROM kn_docs d WHERE NOT EXISTS (SELECT 1 FROM kn_fts f WHERE f.rowid = d.id)").fetchone()[0],
    }
    return {"ok": ok == "ok" and not fk and not any(orphans.values()), "integrity_check": ok,
            "foreign_key_problems": len(fk), "orphans": orphans,
            "versions": [m["version"] for m in applied(c)], "latest": LATEST,
            "core_user_version": c.execute("PRAGMA user_version").fetchone()[0]}
