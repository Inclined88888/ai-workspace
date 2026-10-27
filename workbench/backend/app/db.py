import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'openai',          -- openai | anthropic | ollama
  base_url TEXT NOT NULL,
  api_key_enc TEXT NOT NULL DEFAULT '',
  models TEXT NOT NULL DEFAULT '',              -- optional comma separated fallback list
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  path TEXT NOT NULL,                          -- relative to DATA_DIR/files
  text TEXT NOT NULL DEFAULT '',               -- extracted text, '' when not readable
  note TEXT NOT NULL DEFAULT '',               -- why there is no text, if so
  sha256 TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(name, text, content='files', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
  INSERT INTO files_fts(rowid, name, text) VALUES (new.id, new.name, new.text);
END;
CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
  INSERT INTO files_fts(files_fts, rowid, name, text) VALUES ('delete', old.id, old.name, old.text);
END;

CREATE TABLE IF NOT EXISTS docs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'note',            -- note | document
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, body, tags, content='docs', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
  INSERT INTO docs_fts(rowid, title, body, tags) VALUES (new.id, new.title, new.body, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, title, body, tags) VALUES ('delete', old.id, old.title, old.body, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, title, body, tags) VALUES ('delete', old.id, old.title, old.body, old.tags);
  INSERT INTO docs_fts(rowid, title, body, tags) VALUES (new.id, new.title, new.body, new.tags);
END;

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo',          -- todo | doing | done
  priority INTEGER NOT NULL DEFAULT 0,          -- 0 none, 1 low, 2 medium, 3 high
  due TEXT,                                     -- YYYY-MM-DD
  project TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS calendars (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                           -- local | ics | caldav
  url TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '',
  password_enc TEXT NOT NULL DEFAULT '',
  color TEXT NOT NULL DEFAULT '#6366f1',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_synced TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  calendar_id INTEGER NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
  uid TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  start TEXT NOT NULL,                          -- ISO 8601 (UTC for timed events, date for all day)
  end TEXT NOT NULL,
  all_day INTEGER NOT NULL DEFAULT 0,
  location TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  remote_href TEXT NOT NULL DEFAULT '',
  UNIQUE(calendar_id, uid)
);
CREATE INDEX IF NOT EXISTS events_start ON events(start);

CREATE TABLE IF NOT EXISTS mail_accounts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  imap_host TEXT NOT NULL,
  imap_port INTEGER NOT NULL DEFAULT 993,
  smtp_host TEXT NOT NULL,
  smtp_port INTEGER NOT NULL DEFAULT 465,
  smtp_security TEXT NOT NULL DEFAULT 'ssl',    -- ssl | starttls
  username TEXT NOT NULL,
  password_enc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_drafts (
  id INTEGER PRIMARY KEY,
  account_id INTEGER REFERENCES mail_accounts(id) ON DELETE CASCADE,
  to_addr TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  in_reply_to TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research (
  id INTEGER PRIMARY KEY,
  question TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',       -- running | done | error
  progress TEXT NOT NULL DEFAULT '',
  report TEXT NOT NULL DEFAULT '',
  sources TEXT NOT NULL DEFAULT '[]',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Assistant: conversations from every channel share one memory
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL DEFAULT 'New chat',
  channel TEXT NOT NULL DEFAULT 'web',          -- web | cli | telegram | schedule | agent
  external_id TEXT NOT NULL DEFAULT '',         -- telegram chat id, agent task id, ...
  agent_id INTEGER,
  model TEXT NOT NULL DEFAULT '',
  memory_cursor INTEGER NOT NULL DEFAULT 0,     -- last message id already mined for memories
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_channel ON conversations(channel, external_id);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                           -- user | assistant | tool
  content TEXT NOT NULL DEFAULT '',
  tool_calls TEXT NOT NULL DEFAULT '',          -- JSON (assistant)
  tool_call_id TEXT NOT NULL DEFAULT '',        -- (tool)
  name TEXT NOT NULL DEFAULT '',                -- tool name (tool)
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages WHEN new.role IN ('user', 'assistant') BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages WHEN old.role IN ('user', 'assistant') BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY,
  tool TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT 'web',
  conversation_id INTEGER,
  agent_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',      -- pending | running | approved | denied | failed
  result TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  decided_at TEXT
);

-- Projects (projects.py): a workspace for ongoing work; other tables link to it with project_id
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  goals TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  budget_cents INTEGER NOT NULL DEFAULT 0,
  local_only INTEGER NOT NULL DEFAULT 0,
  permissions TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',        -- active | archived
  deleted_at TEXT,                              -- in the bin since
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_notes (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                           -- decision | question
  text TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  answer TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

-- Missions: planned multi step work (see missions.py)
CREATE TABLE IF NOT EXISTS missions (
  id INTEGER PRIMARY KEY,
  goal TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planning',      -- planning | review | queued | running | waiting_approval | paused | done | partial | failed | cancelled
  control TEXT NOT NULL DEFAULT '',             -- pause | cancel requested while running
  auto_start INTEGER NOT NULL DEFAULT 0,
  conversation_id INTEGER,
  plan TEXT NOT NULL DEFAULT '[]',              -- JSON steps with status, attempts, result, evidence, tool log
  current_step INTEGER NOT NULL DEFAULT 0,
  budget_tool_calls INTEGER NOT NULL DEFAULT 40,
  budget_minutes INTEGER NOT NULL DEFAULT 30,
  budget_cents INTEGER NOT NULL DEFAULT 0,      -- 0 = no spending limit
  used_tool_calls INTEGER NOT NULL DEFAULT 0,
  used_cents REAL NOT NULL DEFAULT 0,
  elapsed_seconds REAL NOT NULL DEFAULT 0,
  report TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  heartbeat_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_events (
  id INTEGER PRIMARY KEY,
  mission_id INTEGER NOT NULL,
  at TEXT NOT NULL,
  step INTEGER,
  kind TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS mission_events_m ON mission_events(mission_id, id);
-- Write actions a mission already did, so a retried or recovered step replays instead of repeating them
CREATE TABLE IF NOT EXISTS mission_actions (
  id INTEGER PRIMARY KEY,
  mission_id INTEGER NOT NULL,
  tool TEXT NOT NULL,
  args_key TEXT NOT NULL,
  ok INTEGER NOT NULL,
  result TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS mission_actions_k ON mission_actions(mission_id, tool, args_key);

-- Computer Use (computer.py): paired companions, one-time pairing codes, the action queue and temporary grants
CREATE TABLE IF NOT EXISTS computer_devices (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  os TEXT NOT NULL DEFAULT '',
  token_hash TEXT NOT NULL UNIQUE,
  info TEXT NOT NULL DEFAULT '{}',
  revoked INTEGER NOT NULL DEFAULT 0,
  paired_at TEXT NOT NULL,
  last_seen TEXT
);
CREATE TABLE IF NOT EXISTS computer_pairing (
  code_hash TEXT PRIMARY KEY,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS computer_actions (
  id INTEGER PRIMARY KEY,
  device_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '{}',
  risk TEXT NOT NULL DEFAULT 'read',
  status TEXT NOT NULL DEFAULT 'queued',        -- queued | dispatched | done | failed | refused | cancelled | uncertain
  cancel INTEGER NOT NULL DEFAULT 0,
  approved INTEGER NOT NULL DEFAULT 0,          -- the user approved this action; the companion re-checks consequential ones
  recovered INTEGER NOT NULL DEFAULT 0,         -- its result arrived after a restart
  result TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  conversation_id INTEGER,
  mission_id INTEGER,
  idem_key TEXT NOT NULL DEFAULT '',
  timeout INTEGER NOT NULL DEFAULT 30,
  created_at TEXT NOT NULL,
  dispatched_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS computer_actions_q ON computer_actions(device_id, status, id);
CREATE TABLE IF NOT EXISTS computer_grants (
  id INTEGER PRIMARY KEY,
  app TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

-- Every tool run (reads included), newest 5,000 kept
CREATE TABLE IF NOT EXISTS tool_runs (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  tool TEXT NOT NULL,
  actor TEXT NOT NULL,
  conversation_id INTEGER,
  mission_id INTEGER,
  ok INTEGER NOT NULL,
  ms INTEGER NOT NULL DEFAULT 0,
  args TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS tool_runs_tool ON tool_runs(tool, id);

-- Who did what: tool actions, approvals, permission and connection changes, sign-ins. Never holds secret values.
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,                          -- user | assistant | agent:<name> | telegram | schedule | system
  action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS audit_at ON audit_log(at);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact',            -- profile (always shown) | fact | preference
  source TEXT NOT NULL DEFAULT 'auto',          -- auto | user | agent | import
  conversation_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- Optional embeddings for meaning-based memory search (memory.py); one row per memory and model
CREATE TABLE IF NOT EXISTS memory_vectors (
  memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  vec BLOB NOT NULL,
  PRIMARY KEY (memory_id, model)
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content, content='memories', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS skills (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL,                    -- when to use it
  steps TEXT NOT NULL,                          -- Markdown procedure
  uses INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'auto',          -- auto | user
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  cron TEXT NOT NULL,                           -- 5 field cron in the workspace timezone
  prompt TEXT NOT NULL,
  deliver TEXT NOT NULL DEFAULT 'inbox',        -- inbox | telegram
  agent_id INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  next_run TEXT,                                -- UTC ISO
  last_run TEXT,
  last_status TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

-- Company: agents in an org chart working on tasks, each with a monthly budget
CREATE TABLE IF NOT EXISTS agents (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  reports_to INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  model TEXT NOT NULL DEFAULT '',               -- model ref; empty = workspace default
  instructions TEXT NOT NULL DEFAULT '',
  monthly_budget_cents INTEGER NOT NULL DEFAULT 0,  -- 0 = no money limit
  monthly_token_limit INTEGER NOT NULL DEFAULT 0,   -- 0 = no token limit
  status TEXT NOT NULL DEFAULT 'active',        -- active | paused
  paused_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  parent_id INTEGER REFERENCES agent_tasks(id) ON DELETE SET NULL,
  created_by TEXT NOT NULL DEFAULT 'board',     -- board | agent:<id>
  status TEXT NOT NULL DEFAULT 'todo',          -- todo | running | done | failed | blocked
  result TEXT NOT NULL DEFAULT '',
  conversation_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  model TEXT NOT NULL,
  agent_id INTEGER,
  conversation_id INTEGER,
  channel TEXT NOT NULL DEFAULT '',
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  cost_cents REAL NOT NULL DEFAULT 0,
  priced INTEGER NOT NULL DEFAULT 0             -- 1 when a price was known for the model
);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return c


def _add_column(table: str, column: str, ddl: str) -> None:
    """Additive migration: add a column to an existing database without touching its data."""
    if column not in {r[1] for r in conn().execute(f"PRAGMA table_info({table})")}:
        conn().execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init() -> None:
    conn().executescript(SCHEMA)
    _add_column("memories", "previous", "TEXT NOT NULL DEFAULT ''")
    _add_column("research", "depth", "INTEGER NOT NULL DEFAULT 3")
    _add_column("memories", "confidence", "REAL NOT NULL DEFAULT 0.7")
    for t in ("conversations", "missions", "memories", "research", "docs", "files", "tasks"):
        _add_column(t, "project_id", "INTEGER")
    _add_column("projects", "isolated", "INTEGER NOT NULL DEFAULT 0")
    _add_column("conversations", "summary", "TEXT NOT NULL DEFAULT ''")
    _add_column("conversations", "summary_upto", "INTEGER NOT NULL DEFAULT 0")
    for col, ddl in (("depends", "INTEGER NOT NULL DEFAULT 0"), ("max_attempts", "INTEGER NOT NULL DEFAULT 2"),
                     ("review_unverified", "INTEGER NOT NULL DEFAULT 0"), ("estimate_cents", "REAL"), ("template", "TEXT NOT NULL DEFAULT ''")):
        _add_column("missions", col, ddl)
    for col, ddl in (("plan", "TEXT NOT NULL DEFAULT '[]'"), ("stats", "TEXT NOT NULL DEFAULT '{}'"),
                     ("skipped", "TEXT NOT NULL DEFAULT '[]'"), ("cancel", "INTEGER NOT NULL DEFAULT 0")):
        _add_column("research", col, ddl)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def all(sql: str, *args: Any) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, *args: Any) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def run(sql: str, *args: Any) -> int:
    cur = conn().execute(sql, args)
    return cur.lastrowid or cur.rowcount


def changed(sql: str, *args: Any) -> int:
    """Rows changed by an UPDATE or DELETE (run() returns the last insert id, which goes stale after updates)."""
    return conn().execute(sql, args).rowcount


def get_setting(key: str, default: str | None = None) -> str | None:
    r = one("SELECT value FROM settings WHERE key = ?", key)
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    run(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        key,
        value,
    )
