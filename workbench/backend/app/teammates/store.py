"""Teammates: named assistants with their own persona, memory scope, tools and routines (feat/grok-style)."""

from __future__ import annotations

import json
import re
import threading

from fastapi import HTTPException

from .. import config, db

SCHEMA = """
CREATE TABLE IF NOT EXISTS tm_sections (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teammates (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT '',
  persona TEXT NOT NULL DEFAULT '',           -- how it works and talks, in the user's words
  avatar TEXT NOT NULL DEFAULT '',            -- one emoji or two letters
  color TEXT NOT NULL DEFAULT 'sun',          -- a theme accent name, never a raw colour
  template TEXT NOT NULL DEFAULT '',
  memory_scope TEXT NOT NULL DEFAULT 'own',   -- own: its notes + shared facts about you | shared: everything you told any teammate
  tools TEXT NOT NULL DEFAULT '[]',           -- allowed tool groups (see TOOL_GROUPS); [] = all
  model TEXT NOT NULL DEFAULT '',
  section_id INTEGER,
  position INTEGER NOT NULL DEFAULT 0,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tm_notes (           -- a teammate's own memory
  id INTEGER PRIMARY KEY,
  teammate_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tm_conversations (
  conversation_id INTEGER PRIMARY KEY,
  teammate_id INTEGER NOT NULL,
  group_id INTEGER
);
CREATE TABLE IF NOT EXISTS tm_routines (
  automation_id INTEGER PRIMARY KEY,
  teammate_id INTEGER NOT NULL
);
"""

# Tool groups a teammate may be given. A tool not matched by any group is always allowed (notes, tasks, memory...).
TOOL_GROUPS: dict[str, dict] = {
    "mail": {"label": "Mail", "match": r"email|mail_"},
    "calendar": {"label": "Calendar", "match": r"event|agenda|calendar|free_time"},
    "web": {"label": "Web search and reading", "match": r"^web_search$|^read_page$"},
    "browser": {"label": "Its browser", "match": r"browse_web|playbook"},
    "computer": {"label": "Its computer (Rooks Desk)", "match": r"use_computer"},
    "files": {"label": "Files and analysis", "match": r"file|spreadsheet|document|image|run_python"},
    "sites": {"label": "Work sites", "match": r"site"},
    "packs": {"label": "Trade, Legal and Study packs", "match": r"^(trade|legal|study)_"},
    "projects": {"label": "Projects", "match": r"project"},
    "automations": {"label": "Routines and schedules", "match": r"schedule|automation|background_job"},
    "team": {"label": "AI company", "match": r"delegate_task|list_team"},
}

TEMPLATES: list[dict] = [
    {"key": "personal", "name": "Personal", "role": "Personal assistant", "avatar": "☀", "color": "sun",
     "persona": "Warm, brief and practical. Looks after my day: mail, calendar, reminders, errands and family plans.",
     "tools": [], "section": "Personal"},
    {"key": "work", "name": "Work", "role": "Work assistant", "avatar": "✦", "color": "ink",
     "persona": "Clear, professional and to the point. Handles my inbox, meetings, documents and follow-ups.",
     "tools": ["mail", "calendar", "web", "files", "projects", "automations"], "section": "Work"},
    {"key": "study", "name": "Study buddy", "role": "Tutor and study partner", "avatar": "✎", "color": "sky",
     "persona": "Patient tutor. Explains step by step, checks maths with real calculations, makes flashcards and cites sources.",
     "tools": ["web", "files", "packs", "projects"], "section": "Study"},
    {"key": "trade", "name": "Trade office", "role": "Office manager for a trade business", "avatar": "⚒", "color": "cherry",
     "persona": "Down-to-earth office manager. Turns site photos and notes into quotes and invoices with GST, chases payments "
                "politely and keeps the job schedule tidy. Never sends anything without my OK.",
     "tools": ["mail", "calendar", "files", "sites", "packs", "projects", "automations"], "section": "Work"},
    {"key": "research", "name": "Researcher", "role": "Research analyst", "avatar": "◎", "color": "leaf",
     "persona": "Thorough and sceptical. Reads widely, compares sources, quotes them and says plainly what is uncertain.",
     "tools": ["web", "browser", "files", "projects"], "section": "Work"},
    {"key": "legal", "name": "Legal desk", "role": "Contract and legislation reader", "avatar": "§", "color": "ink",
     "persona": "Careful reader of contracts and laws. Clause-by-clause notes with sources; always says it is not a lawyer.",
     "tools": ["web", "files", "packs", "projects"], "section": "Work"},
]
COLORS = ("sun", "ink", "sky", "cherry", "leaf", "stone")
TEMPLATE_VERSION = 1

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


def _out(r: dict | None) -> dict:
    if not r:
        raise HTTPException(404, "Teammate not found")
    r = dict(r)
    try:
        r["tools"] = json.loads(r.get("tools") or "[]")
    except ValueError:
        r["tools"] = []
    r["is_default"] = bool(r.get("is_default"))
    return r


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())[:40]
    if not name:
        raise HTTPException(400, "Give your teammate a name")
    return name


def _avatar(value: str, name: str) -> str:
    value = (value or "").strip()
    if value:
        return value[:2]
    return "".join(w[0] for w in name.split()[:2]).upper() or "A"


def _tools(value) -> list[str]:
    return [t for t in (value or []) if t in TOOL_GROUPS]


# Sections ---------------------------------------------------------------------------------------------------------
def sections() -> list[dict]:
    ensure()
    return db.all("SELECT id, name, position FROM tm_sections ORDER BY position, id")


def section_for(name: str) -> int:
    ensure()
    name = (name or "").strip()[:40]
    if not name:
        raise HTTPException(400, "A section needs a name")
    row = db.one("SELECT id FROM tm_sections WHERE lower(name) = lower(?)", name)
    if row:
        return row["id"]
    pos = (db.one("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM tm_sections") or {"p": 1})["p"]
    return db.run("INSERT INTO tm_sections(name, position, created_at) VALUES (?, ?, ?)", name, pos, db.now())


def rename_section(sid: int, name: str) -> dict:
    ensure()
    name = (name or "").strip()[:40]
    if not name:
        raise HTTPException(400, "A section needs a name")
    db.run("UPDATE tm_sections SET name = ? WHERE id = ?", name, sid)
    return {"id": sid, "name": name}


def move_section(sid: int, delta: int) -> list[dict]:
    rows = sections()
    ids = [r["id"] for r in rows]
    if sid not in ids:
        raise HTTPException(404, "Section not found")
    i = ids.index(sid)
    j = max(0, min(len(ids) - 1, i + delta))
    ids.insert(j, ids.pop(i))
    for pos, x in enumerate(ids):
        db.run("UPDATE tm_sections SET position = ? WHERE id = ?", pos, x)
    return sections()


def delete_section(sid: int) -> dict:
    ensure()
    db.run("UPDATE teammates SET section_id = NULL WHERE section_id = ?", sid)
    db.run("DELETE FROM tm_sections WHERE id = ?", sid)
    return {"deleted": sid}


# Teammates --------------------------------------------------------------------------------------------------------
def listing() -> dict:
    ensure()
    seed_default()
    mates = [_out(r) for r in db.all("SELECT * FROM teammates ORDER BY position, id")]
    return {"teammates": mates, "sections": sections(), "tool_groups": [{"id": k, "label": v["label"]} for k, v in TOOL_GROUPS.items()],
            "templates": [{k: t[k] for k in ("key", "name", "role", "avatar", "color", "persona")} for t in TEMPLATES],
            "colors": list(COLORS)}


def get(tid: int) -> dict:
    ensure()
    return _out(db.one("SELECT * FROM teammates WHERE id = ?", tid))


def seed_default() -> None:
    """The first teammate is the assistant everyone already has, under the name set in Settings."""
    ensure()
    if db.one("SELECT 1 FROM teammates LIMIT 1"):
        return
    name = db.get_setting("assistant_name", "Atlas") or "Atlas"
    sec = section_for("Personal")
    db.run("INSERT INTO teammates(name, role, persona, avatar, color, template, section_id, is_default, created_at, updated_at) "
           "VALUES (?, 'Your main assistant', '', ?, 'sun', 'personal', ?, 1, ?, ?)", name, _avatar("", name), sec, db.now(), db.now())


def create(data: dict) -> dict:
    ensure()
    seed_default()
    tpl = next((t for t in TEMPLATES if t["key"] == data.get("template")), None)
    base = {**(tpl or {}), **{k: v for k, v in data.items() if v not in (None, "")}}
    name = _clean_name(base.get("name", ""))
    if db.one("SELECT 1 FROM teammates WHERE lower(name) = lower(?)", name):
        raise HTTPException(409, f"You already have a teammate called {name}")
    sec = data.get("section_id")
    if not sec and (data.get("section") or (tpl or {}).get("section")):
        sec = section_for(data.get("section") or tpl["section"])
    color = base.get("color") if base.get("color") in COLORS else "sun"
    scope = base.get("memory_scope") if base.get("memory_scope") in ("own", "shared") else "own"
    pos = (db.one("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM teammates") or {"p": 1})["p"]
    tid = db.run("INSERT INTO teammates(name, role, persona, avatar, color, template, memory_scope, tools, model, section_id, position, "
                 "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 name, str(base.get("role", ""))[:120], str(base.get("persona", ""))[:4000], _avatar(base.get("avatar", ""), name),
                 color, (tpl or {}).get("key", ""), scope, json.dumps(_tools(base.get("tools"))), str(base.get("model", ""))[:200],
                 sec, pos, db.now(), db.now())
    from . import computer

    computer.ensure_space(tid)
    return get(tid)


def update(tid: int, data: dict) -> dict:
    cur = get(tid)
    sets, args = [], []
    for k in ("role", "persona", "model"):
        if k in data and data[k] is not None:
            sets.append(f"{k} = ?")
            args.append(str(data[k])[:4000])
    if data.get("name") is not None:
        name = _clean_name(data["name"])
        if name.lower() != cur["name"].lower() and db.one("SELECT 1 FROM teammates WHERE lower(name) = lower(?)", name):
            raise HTTPException(409, f"You already have a teammate called {name}")
        sets.append("name = ?")
        args.append(name)
    if data.get("avatar") is not None:
        sets.append("avatar = ?")
        args.append(_avatar(data["avatar"], data.get("name") or cur["name"]))
    if data.get("color") in COLORS:
        sets.append("color = ?")
        args.append(data["color"])
    if data.get("memory_scope") in ("own", "shared"):
        sets.append("memory_scope = ?")
        args.append(data["memory_scope"])
    if data.get("tools") is not None:
        sets.append("tools = ?")
        args.append(json.dumps(_tools(data["tools"])))
    if "section_id" in data:
        sets.append("section_id = ?")
        args.append(data["section_id"] or None)
    if data.get("section"):
        sets.append("section_id = ?")
        args.append(section_for(data["section"]))
    if sets:
        db.run(f"UPDATE teammates SET {', '.join(sets)}, updated_at = ? WHERE id = ?", *args, db.now(), tid)
    return get(tid)


def delete(tid: int) -> dict:
    t = get(tid)
    if t["is_default"]:
        raise HTTPException(400, "Your main assistant can't be deleted. Rename it instead.")
    from .. import automations

    for r in db.all("SELECT automation_id FROM tm_routines WHERE teammate_id = ?", tid):
        try:
            automations.delete(r["automation_id"])
        except HTTPException:
            pass
    from . import memory

    memory.purge(tid)
    for tbl in ("tm_routines", "tm_conversations"):
        db.run(f"DELETE FROM {tbl} WHERE teammate_id = ?", tid)
    db.run("DELETE FROM teammates WHERE id = ?", tid)
    return {"deleted": tid}


def by_name(name: str) -> dict | None:
    ensure()
    r = db.one("SELECT * FROM teammates WHERE lower(name) = lower(?)", (name or "").strip())
    return _out(r) if r else None


def default() -> dict:
    seed_default()
    return _out(db.one("SELECT * FROM teammates ORDER BY is_default DESC, position, id LIMIT 1"))


# Memory (through the adapter in memory.py, swappable for feat/memory-core) ------------------------------------
def notes(tid: int) -> list[dict]:
    from . import memory

    get(tid)
    return memory.notes(tid)


def add_note(tid: int, content: str) -> dict:
    from . import memory

    get(tid)
    return memory.add(tid, content)


def delete_note(tid: int, nid: int) -> dict:
    from . import memory

    return memory.delete(tid, nid)


# Conversations ----------------------------------------------------------------------------------------------------
def link(conversation_id: int, tid: int, group_id: int | None = None) -> None:
    ensure()
    db.run("INSERT OR REPLACE INTO tm_conversations(conversation_id, teammate_id, group_id) VALUES (?, ?, ?)", conversation_id, tid, group_id)


def teammate_for(conversation_id: int | None) -> tuple[dict | None, int | None]:
    """The teammate answering in this conversation (and its group), including routine jobs it owns."""
    if not conversation_id:
        return None, None
    ensure()
    r = db.one("SELECT teammate_id, group_id FROM tm_conversations WHERE conversation_id = ?", conversation_id)
    if r:
        t = db.one("SELECT * FROM teammates WHERE id = ?", r["teammate_id"])
        return (_out(t) if t else None), r["group_id"]
    c = db.one("SELECT external_id FROM conversations WHERE id = ?", conversation_id)
    ext = (c or {}).get("external_id") or ""
    if ext.startswith("job:"):
        try:
            j = db.one("SELECT automation_id FROM jobs WHERE id = ?", int(ext[4:]))
        except Exception:  # noqa: BLE001 - no jobs table in this build
            j = None
        if j and j.get("automation_id"):
            rr = db.one("SELECT teammate_id FROM tm_routines WHERE automation_id = ?", j["automation_id"])
            if rr:
                t = db.one("SELECT * FROM teammates WHERE id = ?", rr["teammate_id"])
                return (_out(t) if t else None), None
    return None, None


def conversations(tid: int) -> list[dict]:
    get(tid)
    return db.all("SELECT c.id, c.title, c.updated_at FROM tm_conversations t JOIN conversations c ON c.id = t.conversation_id "
                  "WHERE t.teammate_id = ? AND t.group_id IS NULL ORDER BY c.updated_at DESC LIMIT 50", tid)


# Templates (share a teammate as a file) ---------------------------------------------------------------------------
def export_template(tid: int, include_notes: bool = False) -> dict:
    """A shareable blueprint: role, persona, look, tools and routines in words. Never secrets, chats or vault items."""
    t = get(tid)
    from .. import automations

    routines = []
    for r in db.all("SELECT automation_id FROM tm_routines WHERE teammate_id = ?", tid):
        try:
            a = automations.get(r["automation_id"])
        except HTTPException:
            continue
        routines.append({"request": a.get("request") or a.get("sentence") or a.get("name"), "name": a.get("name", "")})
    out = {"kind": "ai-workspace.teammate", "version": TEMPLATE_VERSION, "name": t["name"], "role": t["role"], "persona": t["persona"],
           "avatar": t["avatar"], "color": t["color"], "memory_scope": t["memory_scope"], "tools": t["tools"], "routines": routines}
    if include_notes:
        out["notes"] = [n["content"] for n in notes(tid)][:50]
    return out


def import_template(data: dict) -> dict:
    if data.get("kind") != "ai-workspace.teammate":
        raise HTTPException(400, "That file isn't a teammate template")
    if int(data.get("version") or 0) > TEMPLATE_VERSION:
        raise HTTPException(400, "This template was made by a newer version of AI Workspace. Update the app first.")
    name = _clean_name(str(data.get("name", "")))
    base, n = name, 2
    while by_name(name):
        name = f"{base} {n}"[:40]
        n += 1
    t = create({"name": name, "role": data.get("role", ""), "persona": data.get("persona", ""), "avatar": data.get("avatar", ""),
                "color": data.get("color", "sun"), "memory_scope": data.get("memory_scope", "own"), "tools": data.get("tools") or []})
    for note in (data.get("notes") or [])[:50]:
        if isinstance(note, str) and note.strip():
            add_note(t["id"], note)
    # Routines arrive switched off and written out in words, so nothing runs until the new owner looks at it.
    pending = [r.get("request") for r in (data.get("routines") or []) if isinstance(r, dict) and r.get("request")][:20]
    return {**t, "routines_to_review": pending}


def history(tid: int, cid: int) -> dict:
    """A teammate conversation as the chat pane shows it: user and assistant turns, with the choice buttons the
    teammate offered (offer_choices tool calls), its drafts and secure forms attached to the turn that made them."""
    import json as _json

    from . import hooks

    owner, gid = teammate_for(cid)
    if not owner or owner["id"] != tid or gid:
        raise HTTPException(404, "Conversation not found for this teammate")
    out: list[dict] = []
    for r in db.all("SELECT id, role, content, tool_calls, created_at FROM messages WHERE conversation_id = ? ORDER BY id", cid):
        if r["role"] == "user":
            out.append({"role": "user", "text": r["content"], "id": r["id"], "at": r["created_at"]})
            continue
        if r["role"] != "assistant":
            continue
        if not out or out[-1]["role"] != "assistant":
            out.append({"role": "assistant", "text": "", "id": r["id"], "at": r["created_at"]})
        a = out[-1]
        if r["content"]:
            a["text"] = f"{a['text']}\n\n{r['content']}" if a["text"] else r["content"]
        for tc in _json.loads(r["tool_calls"] or "[]"):
            f = tc.get("function", {})
            if f.get("name") == "offer_choices":
                try:
                    args = _json.loads(f.get("arguments") or "{}")
                except ValueError:
                    args = {}
                opts = hooks.parse_choices(args.get("options"))
                if len(opts) >= 2:
                    a["choices"] = {"question": str(args.get("question", ""))[:300], "options": opts}
    return {"conversation_id": cid, "messages": out}
