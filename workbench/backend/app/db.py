import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Model connections (adapters.py, R1). Replaced `providers` in schema version 2: its single `kind` field mixed how a
-- connection authenticates with which wire protocol it speaks. Ids are kept, so model refs "<id>/<model>" survive.
CREATE TABLE IF NOT EXISTS connections (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  auth_kind TEXT NOT NULL,                      -- api_key | oauth_subscription | session_scraping_forbidden | local_ollama | openai_compatible
  protocol TEXT NOT NULL,                       -- responses | chat_completions | anthropic_messages | ollama_native
  base_url TEXT NOT NULL,
  secret_enc TEXT NOT NULL DEFAULT '',          -- API key, or OAuth tokens as JSON (encrypted)
  token_expires_at TEXT,                        -- OAuth access token expiry (item 101)
  state TEXT NOT NULL DEFAULT 'connected',      -- connected | needs_auth | refreshing | disabled (item 108)
  scopes TEXT NOT NULL DEFAULT '',              -- granted OAuth scopes (item 101)
  provider_cap TEXT NOT NULL DEFAULT '',        -- the provider's own usage cap as reported (item 104); never invented here
  residency TEXT NOT NULL DEFAULT 'cloud',      -- local | cloud (item 109)
  models TEXT NOT NULL DEFAULT '',              -- comma separated, added to what the endpoint lists
  enabled INTEGER NOT NULL DEFAULT 1,
  last_ok_at TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact',            -- profile (always shown) | fact | preference
  source TEXT NOT NULL DEFAULT 'auto',          -- auto | user | agent
  conversation_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
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

-- The run log (actions.py): every agent action is one row. Replaced usage, tool_runs, turn_checks and
-- pending_actions in schema version 1 (see migrate_v1).
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,                             -- UTC ISO
  kind TEXT NOT NULL,                           -- turn | model_call | tool_call | decision
  agent_id INTEGER,                             -- company agent; NULL for the assistant itself
  channel TEXT NOT NULL DEFAULT '',             -- web | cli | telegram | schedule | agent | learn:* | research | ...
  conversation_id INTEGER,
  provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',               -- model ref (provider id / model)
  auth_kind TEXT NOT NULL DEFAULT '',
  protocol TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL DEFAULT '',
  effect TEXT NOT NULL DEFAULT '',              -- read | write | external | persistent (tool calls)
  args TEXT NOT NULL DEFAULT '',                -- redacted, for display and trace
  payload_enc TEXT NOT NULL DEFAULT '',         -- encrypted raw arguments, only for held calls that may run later
  latency_ms INTEGER NOT NULL DEFAULT 0,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  usd_cost REAL,                                -- NULL = price unknown
  outcome TEXT NOT NULL,
  failure_reason TEXT NOT NULL DEFAULT '',
  verified INTEGER,                             -- receipts: 1 confirmed, 0 contradicted, NULL nothing to check
  evidence TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  from_text INTEGER NOT NULL DEFAULT 0,         -- tool call recovered from text
  request_id TEXT NOT NULL UNIQUE,              -- idempotency key
  parent_action_id INTEGER REFERENCES actions(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS actions_kind_ts ON actions(kind, ts);
CREATE INDEX IF NOT EXISTS actions_agent_ts ON actions(agent_id, ts);
CREATE INDEX IF NOT EXISTS actions_conversation ON actions(conversation_id);
CREATE INDEX IF NOT EXISTS actions_parent ON actions(parent_action_id);
-- a held tool call is answered at most once
CREATE UNIQUE INDEX IF NOT EXISTS actions_one_decision ON actions(parent_action_id) WHERE kind = 'decision';
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


SCHEMA_VERSION = 2
LEGACY_LEDGERS = ("usage", "tool_runs", "turn_checks", "pending_actions")
LEGACY_TABLES = (*LEGACY_LEDGERS, "providers")


def init() -> None:
    migrate(conn(), config.DB_PATH)


def migrate(c: sqlite3.Connection, path) -> None:
    """Bring a database of any earlier version to SCHEMA_VERSION. Backs the file up before changing data.

    Rollback: stop Workbench, replace workbench.db with the newest workbench.db.pre-v<N>-*.bak, run the previous image.
    """
    version = c.execute("PRAGMA user_version").fetchone()[0]
    legacy = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'") if r[0] in LEGACY_TABLES]
    if version < SCHEMA_VERSION and legacy:  # one backup per upgrade, taken before any data changes
        backup = Path(f"{path}.pre-v{SCHEMA_VERSION}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.bak")
        with sqlite3.connect(backup) as dest:
            c.backup(dest)
    c.executescript(SCHEMA)
    steps = [(1, lambda: migrate_v1(c, [t for t in legacy if t in LEGACY_LEDGERS])), (2, lambda: migrate_v2(c, "providers" in legacy))]
    for target, step in steps:
        if version >= target:
            continue
        c.execute("BEGIN IMMEDIATE")
        try:
            step()
            c.execute(f"PRAGMA user_version = {target}")
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        version = target


def migrate_v1(c: sqlite3.Connection, legacy: list[str]) -> None:
    """Four ledgers into one actions table (R2). Every legacy row becomes one action; nothing is dropped silently."""
    from .security import encrypt

    rid = "lower(hex(randomblob(16)))"
    if "usage" in legacy:
        c.execute(f"INSERT INTO actions(ts, kind, agent_id, conversation_id, channel, model, tokens_in, tokens_out, usd_cost, "
                  f"outcome, request_id) SELECT ts, 'model_call', agent_id, conversation_id, channel, model, prompt_tokens, "
                  f"completion_tokens, CASE WHEN priced = 1 THEN cost_cents / 100.0 END, 'ok', {rid} FROM usage ORDER BY id")
    if "tool_runs" in legacy:
        columns = {row[1] for row in c.execute("PRAGMA table_info(tool_runs)")}
        if {"created_at", "model", "outcome", "verified", "evidence", "from_text"} <= columns:
            c.execute(f"INSERT INTO actions(ts, kind, conversation_id, model, tool, outcome, verified, evidence, from_text, latency_ms, "
                      f"request_id) SELECT created_at, 'tool_call', conversation_id, model, tool, outcome, verified, evidence, from_text, "
                      f"ms, {rid} FROM tool_runs ORDER BY id")
        elif {"id", "at", "tool", "actor", "conversation_id", "mission_id", "ok", "ms", "args", "error"} <= columns:
            # The original ROOKS ledger predates receipts. Execution success is not
            # evidence that the requested change was verified.
            from .redact import redact
            c.row_factory = sqlite3.Row
            for row in c.execute("SELECT * FROM tool_runs ORDER BY id").fetchall():
                metadata = json.dumps({"legacy_tool_run_id": row["id"], "actor": row["actor"],
                                       "mission_id": row["mission_id"]})
                c.execute(f"INSERT INTO actions(ts, kind, conversation_id, channel, tool, outcome, latency_ms, "
                          f"args, failure_reason, detail, request_id) VALUES (?, 'tool_call', ?, 'legacy', ?, ?, ?, ?, ?, ?, {rid})",
                          (row["at"], row["conversation_id"], row["tool"], "ok" if row["ok"] else "tool_error", row["ms"],
                           redact(row["args"]), redact(row["error"]), redact(metadata)))
        else:
            raise RuntimeError("Unsupported legacy tool_runs schema; original rows retained. Restore the previous image or report the table column names.")
    if "turn_checks" in legacy:
        c.execute(f"INSERT INTO actions(ts, kind, conversation_id, model, outcome, failure_reason, request_id) "
                  f"SELECT created_at, 'turn', conversation_id, model, outcome, detail, {rid} FROM turn_checks ORDER BY id")
    if "pending_actions" in legacy:
        c.row_factory = sqlite3.Row
        for p in c.execute("SELECT * FROM pending_actions ORDER BY id").fetchall():
            auto_denied = p["status"] == "denied" and not p["decided_at"]  # unattended runs refuse without a decision
            cur = c.execute(f"INSERT INTO actions(ts, kind, conversation_id, tool, effect, args, payload_enc, detail, outcome, request_id) "
                            f"VALUES (?, 'tool_call', ?, ?, ?, ?, ?, ?, ?, {rid})", (p["created_at"], p["conversation_id"], p["tool"],
                            p["effect"], p["args"][:2000], encrypt(p["args"]), p["reason"], "denied" if auto_denied else "held"))
            if p["status"] == "pending" or auto_denied:
                continue
            answer = "denied" if p["status"] == "denied" else "approved"
            dec = c.execute(f"INSERT INTO actions(ts, kind, conversation_id, tool, outcome, parent_action_id, channel, request_id) "
                            f"VALUES (?, 'decision', ?, ?, ?, ?, 'web', {rid})", (p["decided_at"] or p["created_at"],
                            p["conversation_id"], p["tool"], answer, cur.lastrowid))
            if p["status"] in ("done", "failed"):
                c.execute(f"INSERT INTO actions(ts, kind, conversation_id, tool, outcome, detail, parent_action_id, channel, request_id) "
                          f"VALUES (?, 'tool_call', ?, ?, ?, ?, ?, 'web', {rid})", (p["decided_at"] or p["created_at"],
                          p["conversation_id"], p["tool"], "ok" if p["status"] == "done" else "tool_error", p["result"], dec.lastrowid))
    for table in legacy:
        c.execute(f"DROP TABLE {table}")


OFFICIAL_HOSTS = ("api.openai.com", "api.anthropic.com", "openrouter.ai", "generativelanguage.googleapis.com", "opencode.ai")


def migrate_v2(c: sqlite3.Connection, has_providers: bool) -> None:
    """providers(kind) into connections(auth_kind, protocol), same ids. Behaviour is unchanged: Anthropic connections
    keep the OpenAI compatibility protocol they used; switching to native Messages is a choice in Settings."""
    if not has_providers:
        return
    c.row_factory = sqlite3.Row
    for row in c.execute("SELECT * FROM providers ORDER BY id").fetchall():
        p = dict(zip(row.keys(), row))
        if p["kind"] == "ollama":
            auth, proto, residency = "local_ollama", "ollama_native", "local"
        else:
            auth = "api_key" if any(h in p["base_url"] for h in OFFICIAL_HOSTS) else "openai_compatible"
            proto, residency = "chat_completions", "cloud"
        c.execute("INSERT INTO connections(id, name, auth_kind, protocol, base_url, secret_enc, residency, models, enabled, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (p["id"], p["name"], auth, proto, p["base_url"], p["api_key_enc"], residency, p["models"], p["enabled"], p["created_at"]))
    c.execute("DROP TABLE providers")


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


def get_setting(key: str, default: str | None = None) -> str | None:
    r = one("SELECT value FROM settings WHERE key = ?", key)
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    run(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        key,
        value,
    )
