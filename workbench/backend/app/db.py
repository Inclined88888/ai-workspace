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
-- Proactive check-ins (checkins.py): topics ROOKS keeps an eye on, each run, and each delivery (exactly once)
CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  project_id INTEGER,
  sources TEXT NOT NULL DEFAULT '[]',           -- JSON list: tasks, calendar, missions, approvals, project, page:<url>, search:<query>
  cron TEXT NOT NULL,                           -- when to check, 5 field cron in the workspace timezone
  importance TEXT NOT NULL DEFAULT 'normal',    -- low | normal | high | urgent
  notify TEXT NOT NULL DEFAULT 'inbox',         -- inbox | telegram | call | phone
  quiet_start TEXT NOT NULL DEFAULT '',         -- HH:MM local; empty = the workspace default
  quiet_end TEXT NOT NULL DEFAULT '',
  expires_at TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  budget_cents INTEGER NOT NULL DEFAULT 0,      -- monthly; 0 = only the workspace limits
  next_check_at TEXT,
  last_check_at TEXT,
  last_ok_at TEXT,
  last_status TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  failures INTEGER NOT NULL DEFAULT 0,
  facts TEXT NOT NULL DEFAULT '{}',             -- what was known at the last check: fact key -> hash
  last_briefing TEXT NOT NULL DEFAULT '',
  cancel INTEGER NOT NULL DEFAULT 0,
  retry_slot TEXT,                              -- a failed or interrupted slot being retried keeps its identity
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_runs (
  id INTEGER PRIMARY KEY,
  topic_id INTEGER NOT NULL,
  slot TEXT NOT NULL,                           -- the scheduled time this run answers (or manual-...)
  attempt INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,                         -- running | nothing_new | not_significant | delivered | deferred | limited | failed | interrupted | cancelled | skipped
  new_facts INTEGER NOT NULL DEFAULT 0,
  briefing TEXT NOT NULL DEFAULT '',
  urgency TEXT NOT NULL DEFAULT '',
  written_by TEXT NOT NULL DEFAULT '',          -- model | rules (no model call was made or possible)
  cost_cents REAL NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  UNIQUE(topic_id, slot, attempt)
);
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,                     -- topic:slot:channel; a slot is delivered at most once per channel
  topic_id INTEGER,
  run_id INTEGER,
  channel TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL,                         -- pending | sending | sent | failed | uncertain
  deliver_after TEXT,
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT
);

-- ROOKS reaching out by voice (calls.py): in-app call invitations and optional phone calls
CREATE TABLE IF NOT EXISTS call_invites (
  id INTEGER PRIMARY KEY,
  topic_id INTEGER,
  delivery_id INTEGER,
  title TEXT NOT NULL,
  briefing TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',       -- pending | accepted | declined | snoozed | missed
  snoozes INTEGER NOT NULL DEFAULT 0,
  snooze_until TEXT,
  expires_at TEXT NOT NULL,
  conversation_id INTEGER,
  fallback_sent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS phone_calls (
  id INTEGER PRIMARY KEY,
  delivery_id INTEGER,
  purpose TEXT NOT NULL,                        -- briefing | verification
  provider TEXT NOT NULL,
  to_masked TEXT NOT NULL,                      -- never the full number in history
  provider_ref TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,                         -- placed | failed | refused
  cost_estimate_cents REAL NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
-- Workers: bounded pieces of a mission, each with a role, a contract and its own tools (workers.py)
CREATE TABLE IF NOT EXISTS worker_tasks (
  id INTEGER PRIMARY KEY,
  mission_id INTEGER NOT NULL,                  -- the parent mission: its project, rules and shared budget
  role TEXT NOT NULL,                           -- research | evidence | data | document | coding | computer | review | coordinator
  goal TEXT NOT NULL,
  inputs TEXT NOT NULL DEFAULT '',
  expected TEXT NOT NULL DEFAULT '',            -- the expected output
  acceptance TEXT NOT NULL DEFAULT '',          -- what the check must see to call it done
  tools TEXT NOT NULL DEFAULT '[]',             -- JSON: the tools it may use (within its role and scope)
  scope TEXT NOT NULL DEFAULT 'read',           -- read | draft | write (writes always wait for your approval)
  model TEXT NOT NULL DEFAULT '',
  budget_cents REAL NOT NULL DEFAULT 0,         -- its cap, reserved from the mission's shared budget when it starts
  budget_minutes INTEGER NOT NULL DEFAULT 10,
  max_attempts INTEGER NOT NULL DEFAULT 2,
  after TEXT NOT NULL DEFAULT '[]',             -- JSON: worker task ids that must be done first
  review_of INTEGER,                            -- a review task: the task it checks
  needs_review INTEGER NOT NULL DEFAULT 1,      -- an independent reviewer checks the result before it counts
  user_started INTEGER NOT NULL DEFAULT 0,      -- computer work runs only after you start it
  status TEXT NOT NULL DEFAULT 'queued',        -- queued | running | waiting_approval | in_review | needs_you | done | failed | blocked | cancelled
  attempts INTEGER NOT NULL DEFAULT 0,
  epoch INTEGER NOT NULL DEFAULT 0,             -- lease fencing: bumped on every claim and cancel; a stale run cannot write
  lease_until TEXT,
  reserved_cents REAL NOT NULL DEFAULT 0,       -- what is still held back from the shared budget for it
  used_cents REAL NOT NULL DEFAULT 0,
  conversation_id INTEGER,
  result TEXT NOT NULL DEFAULT '',              -- JSON: summary, output, evidence, tools used
  verification TEXT NOT NULL DEFAULT 'unverified', -- unverified | verified | rejected | accepted (by you)
  check_note TEXT NOT NULL DEFAULT '',
  attention TEXT NOT NULL DEFAULT '',           -- why it needs you
  notes TEXT NOT NULL DEFAULT '[]',             -- context carried into the next attempt
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS worker_tasks_m ON worker_tasks(mission_id, id);
CREATE TABLE IF NOT EXISTS worker_events (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS worker_events_t ON worker_events(task_id, id);

-- The evidence ledger: every claim a research run relied on, where it came from, and how well it holds up (research.py)
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY,
  research_id INTEGER NOT NULL,
  project_id INTEGER,
  claim TEXT NOT NULL,
  basis TEXT NOT NULL,                          -- fact (a source states it) | inference (drawn, not stated) | uncertain
  kind TEXT NOT NULL DEFAULT '',                -- as extracted: fact | figure | opinion | forecast
  source_n INTEGER,                             -- the report's [n]; empty for an inference
  url TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  published TEXT NOT NULL DEFAULT '',           -- the date the page gives, if any
  retrieved_at TEXT NOT NULL DEFAULT '',
  excerpt TEXT NOT NULL DEFAULT '',             -- the exact quote, found in the page text by code
  location TEXT NOT NULL DEFAULT '',            -- where in the page text the quote is
  credibility TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,           -- 0..1 from credibility, freshness, corroboration and verification
  verification TEXT NOT NULL DEFAULT 'unverified', -- supported | not_supported | unclear | unverified
  verified_by TEXT NOT NULL DEFAULT '',
  contradicts TEXT NOT NULL DEFAULT '[]',       -- JSON: ids of entries that disagree with it
  corroborated_by TEXT NOT NULL DEFAULT '[]',   -- JSON: ids of entries from other sites that say the same
  round INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_r ON evidence(research_id, id);

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

-- Work the assistant started from a conversation (coordinator.py): its result is delivered back into that conversation
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                           -- research | mission | coding
  ref_id INTEGER NOT NULL,                      -- research, mission or computer action id
  title TEXT NOT NULL DEFAULT '',
  stage TEXT NOT NULL DEFAULT '',               -- last stage delivered: '' | plan | final
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_open ON jobs(stage, kind);

-- Coding tasks run by an official coding agent on the paired computer (coding.py)
CREATE TABLE IF NOT EXISTS coding_runs (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER,
  agent TEXT NOT NULL,                          -- codex | claude
  task TEXT NOT NULL,
  folder TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',       -- running | done | failed | cancelled
  device_run TEXT NOT NULL DEFAULT '',          -- the companion's id for the run
  progress TEXT NOT NULL DEFAULT '[]',          -- short progress items (commands, files, notes)
  next INTEGER NOT NULL DEFAULT 0,
  result TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  billing TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
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
    _add_column("messages", "job_id", "INTEGER")
    _add_column("approvals", "scope", "TEXT NOT NULL DEFAULT ''")  # the context the request was made in (assistant.request_approval)  # a message the coordinator delivered for this job
    _add_column("conversations", "summary_upto", "INTEGER NOT NULL DEFAULT 0")
    for col, ddl in (("depends", "INTEGER NOT NULL DEFAULT 0"), ("max_attempts", "INTEGER NOT NULL DEFAULT 2"),
                     ("review_unverified", "INTEGER NOT NULL DEFAULT 0"), ("estimate_cents", "REAL"), ("template", "TEXT NOT NULL DEFAULT ''")):
        _add_column("missions", col, ddl)
    for col, ddl in (("plan", "TEXT NOT NULL DEFAULT '[]'"), ("stats", "TEXT NOT NULL DEFAULT '{}'"),
                     ("skipped", "TEXT NOT NULL DEFAULT '[]'"), ("cancel", "INTEGER NOT NULL DEFAULT 0"),
                     ("rounds", "TEXT NOT NULL DEFAULT '[]'"), ("max_rounds", "INTEGER NOT NULL DEFAULT 0"),
                     ("monitor_topic_id", "INTEGER")):
        _add_column("research", col, ddl)
    # v15: one record of every status change of every engine (work.py), written by triggers in the same transaction;
    # and the requests clients may safely retry (idempotency keys)
    conn().executescript("""
CREATE TABLE IF NOT EXISTS work_events (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  ref_id INTEGER NOT NULL,
  old_status TEXT,
  new_status TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS work_events_ref ON work_events(kind, ref_id);
CREATE TABLE IF NOT EXISTS client_requests (
  key TEXT PRIMARY KEY,
  route TEXT NOT NULL,
  conversation_id INTEGER,
  after_message_id INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
""")
    from . import work

    conn().executescript(work.triggers_sql())


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
