"""Tables for the adapt package. Created on first use, so db.py stays untouched. Text a person would not want on disk
in plain form (facts, recordings, playbooks, lessons) is encrypted with the workspace key (security.encrypt)."""

import json
import threading

from .. import db
from ..security import decrypt, encrypt

SCHEMA = """
CREATE TABLE IF NOT EXISTS adapt_facts (
  id INTEGER PRIMARY KEY,
  content_enc TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'fact',        -- about | work | people | preference | format | fact
  source TEXT NOT NULL DEFAULT 'chat',          -- chat | file | task | teach | feedback | user
  evidence_enc TEXT NOT NULL DEFAULT '',        -- where it was learned (a short quote or file name)
  confidence REAL NOT NULL DEFAULT 0.7,
  pinned INTEGER NOT NULL DEFAULT 0,            -- always in context
  uses INTEGER NOT NULL DEFAULT 0,
  fingerprint TEXT NOT NULL DEFAULT '',         -- normalised words, for de-duplication without decrypting everything
  conversation_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adapt_recordings (
  id INTEGER PRIMARY KEY,
  surface TEXT NOT NULL DEFAULT 'browser',      -- browser | desktop | narration
  status TEXT NOT NULL DEFAULT 'recording',     -- recording | stopped | failed
  remote_id TEXT NOT NULL DEFAULT '',           -- the recorder's own session id
  start_url TEXT NOT NULL DEFAULT '',
  events_enc TEXT NOT NULL DEFAULT '',          -- JSON list of normalised events
  narration_enc TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  playbook_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adapt_playbooks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  body_enc TEXT NOT NULL,                       -- JSON: summary, job_kind, surface, start_url, params, steps, notes
  status TEXT NOT NULL DEFAULT 'draft',         -- draft (needs review) | ready
  recording_id INTEGER,
  version INTEGER NOT NULL DEFAULT 1,
  runs INTEGER NOT NULL DEFAULT 0,
  successes INTEGER NOT NULL DEFAULT 0,
  schedule_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adapt_runs (
  id INTEGER PRIMARY KEY,
  playbook_id INTEGER NOT NULL,
  surface TEXT NOT NULL,
  remote_id TEXT NOT NULL DEFAULT '',           -- Rooks Browser / Rooks Desk job id
  status TEXT NOT NULL DEFAULT 'queued',        -- queued | running | waiting | needs_you | done | failed
  values_enc TEXT NOT NULL DEFAULT '',
  goal_enc TEXT NOT NULL DEFAULT '',
  result_enc TEXT NOT NULL DEFAULT '',
  trigger TEXT NOT NULL DEFAULT 'user',         -- user | routine
  rating INTEGER NOT NULL DEFAULT 0,            -- -1 | 0 | 1
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adapt_activity (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                           -- the job kind (jobs.KINDS) or 'other'
  source TEXT NOT NULL,                         -- chat | playbook | file | site | task
  ref TEXT NOT NULL DEFAULT '',                 -- e.g. playbook:3, conversation:12
  summary_enc TEXT NOT NULL DEFAULT '',
  local_weekday INTEGER NOT NULL,               -- 0 Monday .. 6 Sunday, in the workspace time zone
  local_hour INTEGER NOT NULL,
  local_day TEXT NOT NULL,                      -- YYYY-MM-DD in the workspace time zone
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS adapt_activity_kind ON adapt_activity(kind, created_at);
CREATE TABLE IF NOT EXISTS adapt_suggestions (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,                     -- kind + pattern, so the same idea is offered once
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL,
  cron TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  playbook_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open',          -- open | accepted | dismissed
  schedule_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adapt_lessons (
  id INTEGER PRIMARY KEY,
  target_kind TEXT NOT NULL,                    -- message | run | playbook | note | draft | other
  target_id TEXT NOT NULL DEFAULT '',
  rating INTEGER NOT NULL DEFAULT 0,
  lesson_enc TEXT NOT NULL,                     -- the plain English lesson shown as "Learned from your change"
  applied_to TEXT NOT NULL DEFAULT '',          -- fact:12 | playbook:3
  created_at TEXT NOT NULL
);
-- Routines created from a suggestion: unattended, so anything that leaves the workspace is refused (hooks.guard)
CREATE TABLE IF NOT EXISTS adapt_routines (
  schedule_id INTEGER PRIMARY KEY,
  suggestion_id INTEGER,
  playbook_id INTEGER,
  created_at TEXT NOT NULL
);
"""

_ready: set[str] = set()
_lock = threading.Lock()


def ensure() -> None:
    """Create the tables once per database file (tests and the app may point at different files)."""
    from .. import config

    key = str(config.DB_PATH)
    if key in _ready:
        return
    with _lock:
        if key not in _ready:
            db.conn().executescript(SCHEMA)
            _ready.add(key)


def seal(value) -> str:
    """Encrypt text or JSON-able data for a *_enc column."""
    if value is None or value == "":
        return ""
    return encrypt(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


def open_text(value: str) -> str:
    return decrypt(value) if value else ""


def open_json(value: str, default=None):
    text = open_text(value)
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def setting(key: str, default: str = "1") -> str:
    return db.get_setting(key, default) or default


def flag(key: str, default: bool = True) -> bool:
    return setting(key, "1" if default else "0") == "1"
