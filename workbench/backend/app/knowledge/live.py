"""Live wiring (feat/memory-core): the knowledge layer follows the rest of the app as it changes.

* context(ctx, user_text): the budgeted memory block for the assistant's system prompt. Memories marked
  privacy="sensitive" are only included when the turn's model runs on this computer (connection residency "local").
* after_turn(conv_id): index the new chat messages and file the chat under its project.
* sync_once(): incremental pass over tasks, files, sites, events, meetings, projects and the "What I know about you"
  facts (new, changed, removed); loop() runs it every LIVE_SECONDS. Every new item is auto-filed.
* file_item(): auto-filing - attach an item to the Project it names (project name or keywords), recorded in
  kn_filings so the UI can show "Filed under X, change?" and refile_item() can change or undo it.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .. import db
from . import adapters, crypto, graph, index, memory, schema
from .recall import context_for, visible_scopes
from .text import norm_name, words

log = logging.getLogger("knowledge.live")
LIVE_SECONDS = 60


def enabled() -> bool:
    return db.get_setting("kn_live", "1") == "1"


# --- privacy ---------------------------------------------------------------------------------------------------------
def model_is_local(model_ref: str | None) -> bool:
    ref = model_ref or db.get_setting("default_model") or ""
    m = re.match(r"^(\d+)/", ref)
    if not m:
        return False
    row = db.one("SELECT residency, auth_kind FROM connections WHERE id = ?", int(m.group(1)))
    return bool(row) and (row["residency"] == "local" or row["auth_kind"] == "local_ollama")


def context(ctx, user_text: str) -> list[str]:
    try:
        if not enabled() or not (user_text or "").strip():
            return []
        schema.ensure()
        pid = None
        if getattr(ctx, "project_id", None):
            pid = adapters.project_entity(ctx.project_id)
        agent = getattr(ctx, "agent", None)
        scopes = visible_scopes(assistant=str(agent["id"]) if agent else None)
        res = context_for(user_text, budget_tokens=500, scopes=scopes, project_id=pid,
                          allow_sensitive=model_is_local(getattr(ctx, "model", None)), skip_adapt_mirrors=True)
        return [res["block"]] if res["block"] else []
    except Exception as e:  # noqa: BLE001 - never break a chat turn
        log.warning("knowledge context skipped: %s", e)
        return []


# --- auto-filing -----------------------------------------------------------------------------------------------------
def _projects() -> list[dict]:
    out = []
    for e in graph.list_("project", limit=500):
        if (e["attrs"].get("status") or "active") != "active":
            continue
        keys = {norm_name(e["name"])} | {norm_name(k) for k in str(e["attrs"].get("keywords", "")).split(",") if len(k.strip()) > 2}
        out.append({"id": e["id"], "name": e["name"], "keys": {k for k in keys if k}})
    return out


def best_project(text: str) -> tuple[dict | None, str]:
    t = " " + norm_name(text) + " "
    best, why, score = None, "", 0
    for p in _projects():
        for k in p["keys"]:
            if f" {k} " in t and len(k) > score:
                best, why, score = p, f"mentions “{k}”", len(k)
    return best, why


def file_item(item_kind: str, item_ref: str, text: str, entity_id: int | None = None, project_id: int | None = None,
              reason: str = "") -> dict | None:
    """File an item under a project (given, or found from its text). Never overrides a user's change."""
    schema.ensure()
    if db.one("SELECT 1 AS x FROM kn_filings WHERE item_kind = ? AND item_ref = ? AND status IN ('changed','undone','confirmed')",
              item_kind, str(item_ref)):
        return None
    if project_id is None:
        p, reason = best_project(text)
        if not p:
            return None
        project_id = p["id"]
    db.run("INSERT OR IGNORE INTO kn_filings(item_kind, item_ref, target_id, reason, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
           item_kind, str(item_ref), project_id, reason[:200], 0.7, db.now())
    if entity_id and entity_id != project_id:
        graph.link(entity_id, "part_of", project_id, confidence=0.7, source="auto-filing")
    return filing_for(item_kind, item_ref)


def filing_for(item_kind: str, item_ref: str) -> dict | None:
    schema.ensure()
    r = db.one("SELECT f.*, e.name AS project_name FROM kn_filings f JOIN kn_entities e ON e.id = f.target_id "
               "WHERE f.item_kind = ? AND f.item_ref = ? AND f.status IN ('filed','confirmed','changed') ORDER BY f.id DESC LIMIT 1",
               item_kind, str(item_ref))
    if not r:
        return None
    return {"id": r["id"], "item_kind": r["item_kind"], "item_ref": r["item_ref"], "project_id": r["target_id"],
            "project_name": r["project_name"], "reason": r["reason"], "status": r["status"],
            "chip": f"Filed under {r['project_name']}" + ("" if r["status"] != "filed" else ", change?")}


def refile_item(filing_id: int, project_id: int | None) -> dict | None:
    """'Change?' -> another project, or None to unfile. The choice is remembered and never auto-overridden."""
    f = db.one("SELECT * FROM kn_filings WHERE id = ?", filing_id)
    if not f:
        return None
    ent = db.one("SELECT id FROM kn_entities WHERE id IN (SELECT src FROM kn_edges WHERE dst = ? AND rel = 'part_of' AND source = 'auto-filing')",
                 f["target_id"])
    if ent:
        graph.unlink(ent["id"], "part_of", f["target_id"])
    db.run("UPDATE kn_filings SET status = 'undone' WHERE id = ?", filing_id)
    if project_id is None:
        return None
    db.run("INSERT INTO kn_filings(item_kind, item_ref, target_id, reason, confidence, status, created_at) VALUES (?, ?, ?, 'you chose', 1.0, 'changed', ?) "
           "ON CONFLICT(item_kind, item_ref, target_id) DO UPDATE SET status = 'changed', reason = 'you chose', confidence = 1.0",
           f["item_kind"], f["item_ref"], project_id, db.now())
    if ent:
        graph.link(ent["id"], "part_of", project_id, source="user")
    return filing_for(f["item_kind"], f["item_ref"])


def confirm_filing(filing_id: int) -> None:
    db.run("UPDATE kn_filings SET status = 'confirmed' WHERE id = ?", filing_id)


def recent_filings(limit: int = 30) -> list[dict]:
    schema.ensure()
    rows = db.all("SELECT item_kind, item_ref FROM kn_filings WHERE status = 'filed' ORDER BY id DESC LIMIT ?", limit)
    return [f for f in (filing_for(r["item_kind"], r["item_ref"]) for r in rows) if f]


# --- incremental sync ------------------------------------------------------------------------------------------------
def _cursor(name: str) -> str:
    return db.get_setting(f"kn_cursor_{name}", "") or ""


def _set_cursor(name: str, value) -> None:
    db.set_setting(f"kn_cursor_{name}", str(value))


def _has(t: str) -> bool:
    return adapters._has(t)


def after_turn(conv_id: int) -> int:
    """Index new chat messages, link the chat thread, file it under its project."""
    try:
        schema.ensure()
        conv = db.one("SELECT id, title FROM conversations WHERE id = ?", conv_id)
        if not conv:
            return 0
        last = int(db.get_setting(f"kn_conv_{conv_id}", "0") or 0)
        rows = db.all("SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? AND id > ? AND role IN ('user','assistant') "
                      "ORDER BY id", conv_id, last)
        if not rows:
            return 0
        for r in rows:
            index.put("message", str(r["id"]), r["content"][:8000], conv["title"] or "Chat", at=r["created_at"])
        db.set_setting(f"kn_conv_{conv_id}", str(rows[-1]["id"]))
        thread = graph.upsert("thread", conv["title"] or f"Chat {conv_id}", ref=("conversations", conv_id), source="adapter")
        linked = None
        if _has("project_items"):
            linked = db.one("SELECT project_id FROM project_items WHERE kind = 'chat' AND ref = ? ORDER BY id DESC LIMIT 1", str(conv_id))
        pid = adapters.project_entity(linked["project_id"]) if linked else None
        file_item("chat", str(conv_id), " ".join(r["content"] for r in rows if r["role"] == "user"), thread["id"], pid,
                  "the chat is in this project" if pid else "")
        return len(rows)
    except Exception as e:  # noqa: BLE001
        log.warning("knowledge after_turn failed: %s", e)
        return 0


def _sync_projects() -> int:
    if not _has("projects"):
        return 0
    cur = _cursor("projects")
    rows = db.all("SELECT * FROM projects WHERE updated_at > ? ORDER BY updated_at", cur)
    for p in rows:
        graph.upsert("project", p["name"], {"kind": p["kind"], "description": p["description"], "keywords": p["keywords"],
                                            "status": p["status"]}, ref=("projects", p["id"]), source="adapter")
    if rows:
        _set_cursor("projects", rows[-1]["updated_at"])
    n = 0
    last = int(_cursor("project_items") or 0)
    for it in db.all("SELECT * FROM project_items WHERE id > ? ORDER BY id", last):
        pid = adapters.project_entity(it["project_id"])
        if pid and it["kind"] == "person" and it["title"]:
            graph.link(graph.upsert("person", it["title"], source="adapter")["id"], "member_of", pid, source="adapter")
        elif pid and it["kind"] in ("file", "site", "chat") and it["ref"]:
            table, typ = {"file": ("work_files", "document"), "site": ("sites", "site"), "chat": ("conversations", "thread")}[it["kind"]]
            e = graph.upsert(typ, it["title"] or f"{it['kind']} {it['ref']}", ref=(table, it["ref"]), source="adapter")
            graph.link(e["id"], "part_of", pid, source="adapter")
        _set_cursor("project_items", it["id"])
        n += 1
    return len(rows) + n


def _sync_tasks() -> int:
    if not _has("tasks"):
        return 0
    rows = db.all("SELECT * FROM tasks WHERE updated_at > ? ORDER BY updated_at", _cursor("tasks"))
    for t in rows:
        e = graph.upsert("task", t["title"], {"status": t["status"], "due": t["due"], "notes": t["notes"]}, ref=("tasks", t["id"]), source="adapter")
        index.put("task", str(t["id"]), t["notes"], t["title"], at=t["updated_at"])
        p = graph.find(t["project"], "project") if t["project"] else None
        file_item("task", str(t["id"]), f"{t['title']} {t['project']} {t['notes']}", e["id"], p["id"] if p else None,
                  "its project field" if p else "")
        _set_cursor("tasks", t["updated_at"])
    return len(rows)


def _sync_by_id(name: str, table: str, sql: str, fn) -> int:
    if not _has(table):
        return 0
    rows = db.all(sql, int(_cursor(name) or 0))
    for r in rows:
        fn(r)
        _set_cursor(name, r["id"])
    return len(rows)


def _file_row(r):
    e = graph.upsert("document", r["name"], {"kind": r["kind"]}, ref=("work_files", r["id"]), source="adapter")
    index.put("file", str(r["id"]), r["summary"] or "", r["name"], at=r["created_at"])
    file_item("file", str(r["id"]), f"{r['name']} {r['summary']}", e["id"])


def _site_row(r):
    e = graph.upsert("site", r["name"], {"url": r["url"]}, ref=("sites", r["id"]), source="adapter")
    file_item("site", str(r["id"]), r["name"], e["id"])


def _event_row(r):
    e = graph.upsert("event", r["title"] or "Event", {"start": r["start"], "location": r["location"]}, ref=("events", r["id"]), source="adapter")
    index.put("event", str(r["id"]), f"{r['location']} {r['description'][:2000]}", r["title"], at=r["start"])
    file_item("event", str(r["id"]), f"{r['title']} {r['location']} {r['description'][:500]}", e["id"])


def _meeting_row(r):
    e = graph.upsert("event", r["title"], {"kind": "meeting"}, ref=("meetings", r["id"]), source="adapter")
    index.put("meeting", str(r["id"]), (r["transcript"] or "")[:20000], r["title"], at=r["created_at"])
    p = graph.find(r["project"], "project") if r["project"] else None
    file_item("meeting", str(r["id"]), f"{r['title']} {r['project']}", e["id"], p["id"] if p else None)


def _draft_row(r):
    index.put("draft", str(r["id"]), r["body"][:8000], r["subject"] or "Email draft", at=r["updated_at"])
    if r["to_addr"]:
        for addr in re.findall(r"[\w.+-]+@[\w.-]+", r["to_addr"]):
            graph.upsert("person", addr.split("@")[0].replace(".", " ").title(), {"email": addr}, source="adapter")
    file_item("email", f"draft:{r['id']}", f"{r['subject']} {r['body'][:500]}")


def _note_row(r):
    index.put("note", str(r["id"]), r["body"][:20000], r["title"] or "Note", at=r["updated_at"])
    file_item("note", str(r["id"]), f"{r['title']} {r['body'][:500]}")


def _site_record_row(r):
    index.put("site_record", str(r["id"]), f"{r['status']} {r['person']} {r['location']} {r['due']}", r["title"] or r["key"], at=r["last_seen"])
    if r["person"]:
        graph.upsert("person", r["person"], source="adapter")


def _sync_facts() -> int:
    n = adapters.sync_adapt_facts()
    if _has("adapt_facts"):
        live = {f"adapt_fact:{r['id']}" for r in db.all("SELECT id FROM adapt_facts")}
        for m in db.all("SELECT id, source_ref FROM kn_memories WHERE source_ref LIKE 'adapt_fact:%'"):
            if m["source_ref"] not in live:  # forgotten on "What I know about you" -> forgotten here too
                memory.forget(m["id"])
                n += 1
    return n


def sync_once() -> dict:
    schema.ensure()
    out = {"projects": _sync_projects(), "tasks": _sync_tasks(), "facts": _sync_facts()}
    out["files"] = _sync_by_id("files", "work_files", "SELECT * FROM work_files WHERE id > ? ORDER BY id LIMIT 500", _file_row)
    out["sites"] = _sync_by_id("sites", "sites", "SELECT * FROM sites WHERE id > ? ORDER BY id LIMIT 500", _site_row)
    out["events"] = _sync_by_id("events", "events", "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 1000", _event_row)
    out["meetings"] = _sync_by_id("meetings", "meetings", "SELECT * FROM meetings WHERE id > ? ORDER BY id LIMIT 200", _meeting_row)
    out["drafts"] = _sync_by_id("drafts", "mail_drafts", "SELECT * FROM mail_drafts WHERE id > ? ORDER BY id LIMIT 500", _draft_row)
    out["notes"] = _sync_by_id("notes", "docs", "SELECT * FROM docs WHERE id > ? ORDER BY id LIMIT 500", _note_row)
    out["site_records"] = _sync_by_id("site_records", "site_records", "SELECT * FROM site_records WHERE id > ? ORDER BY id LIMIT 1000", _site_record_row)
    return out


async def loop() -> None:
    while True:
        try:
            if enabled():
                await asyncio.to_thread(_sync_thread)
        except Exception as e:  # noqa: BLE001
            log.warning("knowledge sync failed: %s", e)
        await asyncio.sleep(LIVE_SECONDS)


def _sync_thread() -> None:
    try:
        sync_once()
    finally:
        c = getattr(db._local, "conn", None)
        if c is not None:
            c.close()
            db._local.conn = None
