"""Tables for packs. Created on first use so db.py stays untouched (feat/v02-packs)."""

import json
import threading

from .. import config, db

SCHEMA = """
CREATE TABLE IF NOT EXISTS packs_installed (
  pack_id TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  skills TEXT NOT NULL DEFAULT '[]',            -- hub_skills ids installed for this pack
  gate TEXT NOT NULL DEFAULT '{}',              -- per-skill security gate result (risk, reasons)
  installed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pack_settings (
  pack_id TEXT PRIMARY KEY,
  data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS trade_docs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                           -- quote | invoice
  number TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',         -- quote: draft | sent | accepted | declined; invoice: draft | sent | paid
  customer TEXT NOT NULL DEFAULT '{}',          -- name, email, phone, address
  title TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  items TEXT NOT NULL DEFAULT '[]',             -- [{kind: labour|materials|other, description, qty, unit, unit_cost, markup?}]
  totals TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT '{}',            -- what it was drafted from: description, photo file id, voice transcript
  quote_id INTEGER,                             -- invoice made from this quote
  issued_on TEXT NOT NULL,
  due_on TEXT NOT NULL DEFAULT '',
  pdf_file_id INTEGER,
  pushed TEXT NOT NULL DEFAULT '{}',            -- {servicem8: {...}, xero: {...}} after the user approved a push
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_reminders (
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL,
  tone TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legal_clauses (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',              -- where it came from: a file and location, or "typed"
  uses INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_cards (
  id INTEGER PRIMARY KEY,
  deck TEXT NOT NULL DEFAULT 'General',
  front TEXT NOT NULL,
  back TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  ease REAL NOT NULL DEFAULT 2.5,               -- SM-2
  interval_days REAL NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  due_at TEXT NOT NULL,
  last_reviewed_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_refs (
  id INTEGER PRIMARY KEY,
  data TEXT NOT NULL,                           -- CSL-like: type, title, authors [{family, given}], year, container, volume, issue, pages, doi, url, publisher
  key TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_entries (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  attachments TEXT NOT NULL DEFAULT '[]',       -- work_files ids
  amendments TEXT NOT NULL DEFAULT '[]',        -- [{at, title, body, tags}] earlier versions, oldest first
  entry_date TEXT NOT NULL,                     -- the day it records (local), set when created
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

_ready: set[str] = set()
_lock = threading.Lock()


def ensure() -> None:
    key = str(config.DB_PATH)
    if key in _ready:
        return
    with _lock:
        if key not in _ready:
            db.conn().executescript(SCHEMA)
            _ready.add(key)


def settings(pack_id: str, defaults: dict) -> dict:
    ensure()
    row = db.one("SELECT data FROM pack_settings WHERE pack_id = ?", pack_id)
    return {**defaults, **(json.loads(row["data"]) if row else {})}


def save_settings(pack_id: str, data: dict) -> None:
    ensure()
    db.run("INSERT INTO pack_settings (pack_id, data) VALUES (?, ?) ON CONFLICT(pack_id) DO UPDATE SET data = excluded.data",
           pack_id, json.dumps(data))
