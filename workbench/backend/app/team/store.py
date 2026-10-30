"""Team data adapter (feat/team-lead).

Everything that touches the team tables lives here, behind small functions, so the storage can move onto the shared
entity graph (feat/memory-core: Person, Project, Task) without touching the rest of the team code. ``person_entity`` and
``task_entity`` give the mapping. Contact details are encrypted at rest with the workspace key.

Privacy: this data stays on the team lead's computer. Only tasks assigned to a member (and that member's updates) are
shared through AI Workspace Cloud, and only when the lead uses Team sync or member links. Nothing about members'
devices, screens or locations is collected anywhere.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timezone

from fastapi import HTTPException

from .. import config, db
from ..security import decrypt, encrypt

SCHEMA = """
CREATE TABLE IF NOT EXISTS team_members (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT '',
  skills TEXT NOT NULL DEFAULT '',            -- comma separated
  contact_enc TEXT NOT NULL DEFAULT '',       -- encrypted JSON: email, phone, telegram, slack
  channel TEXT NOT NULL DEFAULT 'email',      -- email | sms | telegram | slack | link | app
  hours_per_week REAL NOT NULL DEFAULT 38,
  work_days TEXT NOT NULL DEFAULT 'mon,tue,wed,thu,fri',
  away TEXT NOT NULL DEFAULT '[]',            -- [{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "note": ""}]
  has_app INTEGER NOT NULL DEFAULT 0,         -- uses AI Workspace with a Team seat (sees tasks in their app)
  link_url_enc TEXT NOT NULL DEFAULT '',      -- their private member page (no app needed)
  notes TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  invited_at TEXT,
  last_heard_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  assignee_id INTEGER REFERENCES team_members(id) ON DELETE SET NULL,   -- NULL: mine (solo) or not assigned yet
  mine INTEGER NOT NULL DEFAULT 0,            -- the lead's own task ("My tasks", free)
  project_id INTEGER,
  project TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo',        -- todo | doing | blocked | done
  percent INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 0,        -- 0 normal, 1 low, 2 high, 3 urgent
  start TEXT,                                 -- YYYY-MM-DD
  due TEXT,                                   -- YYYY-MM-DD
  estimate_hours REAL NOT NULL DEFAULT 2,
  checklist TEXT NOT NULL DEFAULT '[]',       -- [{"text": "", "done": false}]
  files TEXT NOT NULL DEFAULT '[]',           -- workspace file ids
  skills TEXT NOT NULL DEFAULT '',            -- skills this task needs (comma separated, optional)
  source TEXT NOT NULL DEFAULT 'manual',      -- manual | chat | site | jira | asana | trello
  external_ref TEXT NOT NULL DEFAULT '',      -- site:<id>:<key> | jira:<key> | asana:<gid> | trello:<id>
  blocked_reason TEXT NOT NULL DEFAULT '',
  cloud_id TEXT NOT NULL DEFAULT '',
  synced_at TEXT,
  assigned_at TEXT,
  notified_at TEXT,
  last_progress_at TEXT,
  done_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS team_tasks_ext ON team_tasks(external_ref) WHERE external_ref != '';
CREATE INDEX IF NOT EXISTS team_tasks_assignee ON team_tasks(assignee_id, status);
CREATE TABLE IF NOT EXISTS team_updates (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES team_tasks(id) ON DELETE CASCADE,
  member_id INTEGER,
  source TEXT NOT NULL,                       -- leader | email | sms | telegram | slack | link | app | site | jira | asana | trello
  status TEXT NOT NULL DEFAULT '',
  percent INTEGER,
  note TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '',               -- message id / cloud update id, so a reply is applied once
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS team_updates_ref ON team_updates(source, ref) WHERE ref != '';
CREATE TABLE IF NOT EXISTS team_suggestions (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                         -- assign | rebalance | next
  task_id INTEGER REFERENCES team_tasks(id) ON DELETE CASCADE,
  member_id INTEGER REFERENCES team_members(id) ON DELETE CASCADE,
  from_member_id INTEGER,
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',     -- pending | approved | dismissed | auto
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS team_outbox (
  id INTEGER PRIMARY KEY,
  member_id INTEGER REFERENCES team_members(id) ON DELETE CASCADE,
  task_id INTEGER REFERENCES team_tasks(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,                         -- assign | nudge | plan | invite
  channel TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'waiting',     -- waiting (for approval) | sending | sent | copy (send it yourself) | failed | cancelled
  why TEXT NOT NULL DEFAULT '',               -- why it waits, or why it went out on its own
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT
);
CREATE TABLE IF NOT EXISTS team_channel_ok (
  member_id INTEGER NOT NULL REFERENCES team_members(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  PRIMARY KEY (member_id, channel)
);
CREATE TABLE IF NOT EXISTS team_notices (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                         -- update | late | at_risk | stuck | idle | digest | suggestion
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  task_id INTEGER,
  member_id INTEGER,
  dedupe TEXT NOT NULL DEFAULT '',
  seen INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS team_notices_dedupe ON team_notices(dedupe) WHERE dedupe != '';
"""
STATUSES = ("todo", "doing", "blocked", "done")
CHANNELS = ("email", "sms", "telegram", "slack", "link", "app")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
CONTACT_KEYS = ("email", "phone", "telegram", "slack")

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


def today() -> date:
    return date.today()


def now() -> str:
    return db.now()


def setting(key: str, default: str = "") -> str:
    return db.get_setting(f"team_{key}", default) or default


def set_setting(key: str, value: str) -> None:
    db.set_setting(f"team_{key}", value)


def _seal(d: dict) -> str:
    d = {k: str(v).strip() for k, v in d.items() if k in CONTACT_KEYS and str(v or "").strip()}
    return encrypt(json.dumps(d)) if d else ""


def _unseal(v: str) -> dict:
    try:
        return json.loads(decrypt(v)) if v else {}
    except Exception:  # noqa: BLE001 - a key change must not break the board
        return {}


def _mirror(fn: str, *args) -> None:
    """Keep the shared knowledge graph (feat/memory-core) in step. Optional; never raises."""
    from . import memory

    getattr(memory, fn)(*args)


# --- members ---------------------------------------------------------------------------------------------------------
def _member_out(r: dict) -> dict:
    m = dict(r)
    m["contact"] = _unseal(m.pop("contact_enc", ""))
    link = m.pop("link_url_enc", "")
    m["link_url"] = decrypt(link) if link else ""
    m["skills"] = [s.strip() for s in (m["skills"] or "").split(",") if s.strip()]
    m["work_days"] = [d for d in (m["work_days"] or "").split(",") if d in DAYS]
    m["away"] = json.loads(m["away"] or "[]")
    m["has_app"] = bool(m["has_app"])
    m["active"] = bool(m["active"])
    m["first_name"] = (m["name"].split() or [""])[0]
    return m


def members(include_inactive: bool = False) -> list[dict]:
    ensure()
    q = "SELECT * FROM team_members" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY name COLLATE NOCASE"
    return [_member_out(r) for r in db.all(q)]


def member(mid: int) -> dict:
    ensure()
    r = db.one("SELECT * FROM team_members WHERE id = ?", int(mid))
    if not r:
        raise HTTPException(404, "Team member not found")
    return _member_out(r)


def _member_cols(data: dict, old: dict | None = None) -> dict:
    cols: dict = {}
    if "name" in data:
        name = str(data["name"] or "").strip()
        if len(name) < 1:
            raise HTTPException(400, "Give the team member a name.")
        cols["name"] = name[:120]
    if "role" in data:
        cols["role"] = str(data["role"] or "").strip()[:120]
    if "skills" in data:
        sk = data["skills"]
        sk = sk if isinstance(sk, list) else re.split(r"[,;\n]", str(sk or ""))
        cols["skills"] = ", ".join(dict.fromkeys(s.strip().lower() for s in sk if s.strip()))[:600]
    if "contact" in data or any(k in data for k in CONTACT_KEYS):
        c = dict((old or {}).get("contact") or {})
        c.update({k: v for k, v in (data.get("contact") or {}).items() if k in CONTACT_KEYS})
        c.update({k: data[k] for k in CONTACT_KEYS if k in data})
        if c.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(c["email"]).strip()):
            raise HTTPException(400, "That email address doesn't look right.")
        cols["contact_enc"] = _seal(c)
    if "channel" in data:
        if data["channel"] not in CHANNELS:
            raise HTTPException(400, "Choose email, text, Telegram, Slack, their private link, or the app.")
        cols["channel"] = data["channel"]
    if "hours_per_week" in data and data["hours_per_week"] is not None:
        cols["hours_per_week"] = max(1.0, min(80.0, float(data["hours_per_week"])))
    if "work_days" in data and data["work_days"] is not None:
        wd = data["work_days"] if isinstance(data["work_days"], list) else str(data["work_days"]).split(",")
        cols["work_days"] = ",".join(d for d in DAYS if d in {x.strip().lower()[:3] for x in wd})
    if "away" in data and data["away"] is not None:
        cols["away"] = json.dumps([{"from": str(a.get("from", ""))[:10], "to": str(a.get("to") or a.get("from", ""))[:10],
                                    "note": str(a.get("note", ""))[:120]} for a in data["away"] if a.get("from")][:50])
    for k in ("has_app", "active"):
        if k in data and data[k] is not None:
            cols[k] = 1 if data[k] else 0
    if "notes" in data:
        cols["notes"] = str(data["notes"] or "")[:2000]
    return cols


def add_member(data: dict) -> dict:
    ensure()
    if not str(data.get("name") or "").strip():
        raise HTTPException(400, "Give the team member a name.")
    cols = _member_cols(data)
    cols.setdefault("channel", "email" if (data.get("contact") or {}).get("email") or data.get("email") else "link")
    cols.update(created_at=now(), updated_at=now())
    mid = db.run(f"INSERT INTO team_members({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", *cols.values())
    m = member(mid)
    _mirror("person", m)
    return m


def update_member(mid: int, data: dict) -> dict:
    old = member(mid)
    cols = _member_cols(data, old)
    if cols:
        cols["updated_at"] = now()
        db.run(f"UPDATE team_members SET {', '.join(k + ' = ?' for k in cols)} WHERE id = ?", *cols.values(), int(mid))
        _mirror("person", member(mid))
    return member(mid)


def set_member_link(mid: int, url: str) -> None:
    db.run("UPDATE team_members SET link_url_enc = ?, updated_at = ? WHERE id = ?", encrypt(url) if url else "", now(), int(mid))


def touch_member(mid: int, **cols) -> None:
    if cols:
        db.run(f"UPDATE team_members SET {', '.join(k + ' = ?' for k in cols)} WHERE id = ?", *cols.values(), int(mid))


def remove_member(mid: int) -> dict:
    member(mid)
    db.run("UPDATE team_tasks SET assignee_id = NULL, updated_at = ? WHERE assignee_id = ? AND status != 'done'", now(), int(mid))
    db.run("DELETE FROM team_members WHERE id = ?", int(mid))
    _mirror("forget", "team_members", mid)
    return {"ok": True}


def find_member(name_or_contact: str) -> dict | None:
    """By name (full or first), email, phone or Telegram chat id."""
    s = (name_or_contact or "").strip().lower()
    if not s:
        return None
    ms = members()
    digits = re.sub(r"\D", "", s)
    for m in ms:
        c = m["contact"]
        if s in (m["name"].lower(), str(c.get("email", "")).lower(), str(c.get("telegram", "")).lower()):
            return m
        if digits and len(digits) >= 6 and re.sub(r"\D", "", str(c.get("phone", "")))[-9:] == digits[-9:]:
            return m
    firsts = [m for m in ms if m["first_name"].lower() == s]
    return firsts[0] if len(firsts) == 1 else None


# --- tasks -----------------------------------------------------------------------------------------------------------
def _task_out(r: dict) -> dict:
    t = dict(r)
    t["checklist"] = json.loads(t["checklist"] or "[]")
    t["files"] = json.loads(t["files"] or "[]")
    t["skills"] = [s.strip() for s in (t["skills"] or "").split(",") if s.strip()]
    t["mine"] = bool(t["mine"])
    return t


def tasks(*, assignee_id: int | None = None, status: str = "", include_done: bool = True, project_id: int | None = None,
          mine: bool | None = None, team_only: bool = False) -> list[dict]:
    ensure()
    q, args = "SELECT * FROM team_tasks WHERE 1 = 1", []
    if assignee_id is not None:
        q += " AND assignee_id = ?"
        args.append(int(assignee_id))
    if status:
        q += " AND status = ?"
        args.append(status)
    if not include_done:
        q += " AND status != 'done'"
    if project_id is not None:
        q += " AND project_id = ?"
        args.append(int(project_id))
    if mine is not None:
        q += " AND mine = ?"
        args.append(1 if mine else 0)
    if team_only:
        q += " AND mine = 0"
    q += " ORDER BY status = 'done', COALESCE(due, '9999'), priority DESC, id"
    return [_task_out(r) for r in db.all(q, *args)]


def task(tid: int) -> dict:
    ensure()
    r = db.one("SELECT * FROM team_tasks WHERE id = ?", int(tid))
    if not r:
        raise HTTPException(404, "Task not found")
    return _task_out(r)


def task_by_external(ref: str) -> dict | None:
    ensure()
    r = db.one("SELECT * FROM team_tasks WHERE external_ref = ?", ref)
    return _task_out(r) if r else None


def _project_name(pid) -> str:
    if not pid:
        return ""
    try:
        from .. import projects

        return projects.get(int(pid))["name"]
    except Exception:  # noqa: BLE001 - projects are optional
        return ""


def _date(v) -> str | None:
    if v in (None, ""):
        return None
    s = str(v)[:10]
    try:
        date.fromisoformat(s)
    except ValueError as e:
        raise HTTPException(400, "Dates look like 2026-11-06.") from e
    return s


def _task_cols(data: dict) -> dict:
    cols: dict = {}
    for k in ("title", "notes", "project", "blocked_reason", "source", "external_ref", "cloud_id"):
        if k in data and data[k] is not None:
            cols[k] = str(data[k]).strip()[:300 if k == "title" else 10000]
    if "title" in cols and not cols["title"]:
        raise HTTPException(400, "Give the task a short title.")
    if "status" in data and data["status"] is not None:
        if data["status"] not in STATUSES:
            raise HTTPException(400, "Status is todo, doing, blocked or done.")
        cols["status"] = data["status"]
    for k in ("percent", "priority"):
        if k in data and data[k] is not None:
            cols[k] = max(0, min(100 if k == "percent" else 3, int(data[k])))
    for k in ("due", "start"):
        if k in data:
            cols[k] = _date(data[k])
    if "estimate_hours" in data and data["estimate_hours"] is not None:
        cols["estimate_hours"] = max(0.25, min(400.0, float(data["estimate_hours"])))
    if "assignee_id" in data:
        a = data["assignee_id"]
        if a not in (None, "", 0):
            member(int(a))
        cols["assignee_id"] = int(a) if a not in (None, "", 0) else None
    if "mine" in data and data["mine"] is not None:
        cols["mine"] = 1 if data["mine"] else 0
    if "project_id" in data:
        pid = data["project_id"]
        cols["project_id"] = int(pid) if pid not in (None, "", 0) else None
        if cols["project_id"] and "project" not in data:
            cols["project"] = _project_name(cols["project_id"])
    if "checklist" in data and data["checklist"] is not None:
        cols["checklist"] = json.dumps([{"text": str(c.get("text", ""))[:300], "done": bool(c.get("done"))}
                                        for c in data["checklist"] if str(c.get("text", "")).strip()][:100])
    if "files" in data and data["files"] is not None:
        cols["files"] = json.dumps([int(f) for f in data["files"] if str(f).isdigit()][:50])
    if "skills" in data and data["skills"] is not None:
        sk = data["skills"] if isinstance(data["skills"], list) else str(data["skills"]).split(",")
        cols["skills"] = ", ".join(s.strip().lower() for s in sk if s.strip())[:300]
    return cols


def add_task(data: dict) -> dict:
    ensure()
    cols = _task_cols({"status": "todo", **data})
    if not cols.get("title"):
        raise HTTPException(400, "Give the task a short title.")
    if cols.get("assignee_id"):
        cols["assigned_at"] = now()
        cols["mine"] = 0
    elif "mine" not in cols:
        cols["mine"] = 1 if not members() else 0
    cols.update(created_at=now(), updated_at=now())
    tid = db.run(f"INSERT INTO team_tasks({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", *cols.values())
    t = task(tid)
    _mirror("task", t)
    return t


def update_task(tid: int, data: dict) -> dict:
    old = task(tid)
    cols = _task_cols(data)
    if "assignee_id" in cols and cols["assignee_id"] != old["assignee_id"]:
        cols["assigned_at"] = now() if cols["assignee_id"] else None
        cols["notified_at"] = None
        if cols["assignee_id"]:
            cols["mine"] = 0
    if cols.get("status") == "done" or (cols.get("percent") or 0) >= 100:
        cols.update(status="done", percent=100, done_at=old["done_at"] or now(), blocked_reason="")
    elif cols.get("status") and cols["status"] != "done":
        cols["done_at"] = None
        if cols["status"] != "blocked":
            cols.setdefault("blocked_reason", "")
    if any(k in cols for k in ("status", "percent", "checklist")):
        cols["last_progress_at"] = now()
    if cols:
        cols["updated_at"] = now()
        db.run(f"UPDATE team_tasks SET {', '.join(k + ' = ?' for k in cols)} WHERE id = ?", *cols.values(), int(tid))
        _mirror("task", task(tid))
    return task(tid)


def delete_task(tid: int) -> dict:
    task(tid)
    db.run("DELETE FROM team_tasks WHERE id = ?", int(tid))
    _mirror("forget", "team_tasks", tid)
    return {"ok": True}


def add_update(tid: int, source: str, *, member_id: int | None = None, status: str = "", percent: int | None = None,
               note: str = "", ref: str = "") -> dict | None:
    """Record one progress update and apply it to the task. Returns None when this ref was already applied."""
    ensure()
    if ref and db.one("SELECT 1 FROM team_updates WHERE source = ? AND ref = ?", source, ref):
        return None
    patch: dict = {}
    if status:
        patch["status"] = status
        if status == "blocked" and (note or not task(tid)["blocked_reason"]):
            patch["blocked_reason"] = note[:500]
    if percent is not None:
        patch["percent"] = percent
        if not status and 0 < percent < 100 and task(tid)["status"] in ("todo", "blocked"):
            patch["status"] = "doing"
    if patch:
        update_task(tid, patch)
    else:
        db.run("UPDATE team_tasks SET last_progress_at = ?, updated_at = ? WHERE id = ?", now(), now(), int(tid))
    uid = db.run("INSERT INTO team_updates(task_id, member_id, source, status, percent, note, ref, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", int(tid), member_id, source, status, percent, note[:2000], ref, now())
    if member_id:
        touch_member(member_id, last_heard_at=now())
    return db.one("SELECT * FROM team_updates WHERE id = ?", uid)


def updates(tid: int | None = None, member_id: int | None = None, limit: int = 50) -> list[dict]:
    ensure()
    q, args = "SELECT u.*, t.title AS task_title FROM team_updates u JOIN team_tasks t ON t.id = u.task_id WHERE 1 = 1", []
    if tid is not None:
        q += " AND u.task_id = ?"
        args.append(int(tid))
    if member_id is not None:
        q += " AND (u.member_id = ? OR t.assignee_id = ?)"
        args += [int(member_id), int(member_id)]
    return db.all(q + " ORDER BY u.id DESC LIMIT ?", *args, int(limit))


# --- notices (for the lead) --------------------------------------------------------------------------------------------
def notice(kind: str, title: str, body: str = "", *, task_id: int | None = None, member_id: int | None = None,
           dedupe: str = "") -> int | None:
    ensure()
    if dedupe and db.one("SELECT 1 FROM team_notices WHERE dedupe = ?", dedupe):
        return None
    nid = db.run("INSERT INTO team_notices(kind, title, body, task_id, member_id, dedupe, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 kind, title[:200], body[:2000], task_id, member_id, dedupe, now())
    try:  # the lead's phone (only when they turned phone notifications on)
        from .. import push

        push.notify(title, body[:160] or title, kind="team", notice_id=nid, task_id=task_id or 0)
    except Exception:  # noqa: BLE001 - a notification never breaks tracking
        pass
    return nid


def notices(after: int = 0, unseen_only: bool = False, limit: int = 50) -> list[dict]:
    ensure()
    q = "SELECT * FROM team_notices WHERE id > ?" + (" AND seen = 0" if unseen_only else "")
    return db.all(q + " ORDER BY id DESC LIMIT ?", int(after), int(limit))


def mark_seen(ids: list[int] | None = None) -> None:
    if ids:
        db.run(f"UPDATE team_notices SET seen = 1 WHERE id IN ({', '.join('?' * len(ids))})", *[int(i) for i in ids])
    else:
        db.run("UPDATE team_notices SET seen = 1")


# --- mapping onto the shared entity graph (feat/memory-core) ---------------------------------------------------------
def person_entity(m: dict) -> dict:
    """A team member as a Person entity. Contact details are only included for the owner's own store."""
    return {"type": "Person", "id": f"team_member:{m['id']}", "name": m["name"], "role": m["role"], "skills": m["skills"],
            "relation": "team_member", "attributes": {"hours_per_week": m["hours_per_week"], "work_days": m["work_days"],
                                                       "channel": m["channel"]}, "contact": m.get("contact", {})}


def task_entity(t: dict) -> dict:
    return {"type": "Task", "id": f"team_task:{t['id']}", "title": t["title"], "status": t["status"], "due": t["due"],
            "priority": t["priority"], "percent": t["percent"],
            "assignee": f"team_member:{t['assignee_id']}" if t["assignee_id"] else ("owner" if t["mine"] else None),
            "project": f"project:{t['project_id']}" if t["project_id"] else None, "source": t["source"]}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
