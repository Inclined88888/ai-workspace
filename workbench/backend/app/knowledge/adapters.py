"""Backwards-compatible adapters: existing stores stay the source of truth; the graph and index point at them.

backfill() runs once as migration 3; sync_*() can be called again at any time (idempotent, keyed by ref).
"""
from __future__ import annotations

import logging
import sqlite3

from .. import db
from ..security import decrypt
from . import graph, index, memory

log = logging.getLogger("knowledge.adapters")


def _has(table: str) -> bool:
    return bool(db.one("SELECT 1 AS x FROM sqlite_master WHERE type IN ('table','view') AND name = ?", table))


def sync_projects() -> int:
    n = 0
    if not _has("projects"):
        return 0
    for p in db.all("SELECT * FROM projects"):
        e = graph.upsert("project", p["name"], {"kind": p["kind"], "description": p["description"], "keywords": p["keywords"],
                                                "status": p["status"]}, ref=("projects", p["id"]), source="adapter")
        n += 1
        for it in db.all("SELECT * FROM project_items WHERE project_id = ?", p["id"]):
            body = ""
            try:
                body = decrypt(it["body_enc"]) if it["body_enc"] else ""
            except Exception:  # noqa: BLE001
                body = ""
            if it["kind"] == "person" and it["title"]:
                person = graph.upsert("person", it["title"], {"role": body}, source="adapter")
                graph.link(person["id"], "member_of", e["id"], source="adapter")
            elif it["kind"] == "note" and (body or it["title"]):
                memory.remember(f"{it['title']}: {body}".strip(": ")[:900], "project", project_id=e["id"], confidence=0.9,
                                source_kind="adapter", source_ref=f"project_item:{it['id']}")
            elif it["kind"] == "file" and it["ref"]:
                d = graph.upsert("document", it["title"] or f"File {it['ref']}", ref=("work_files", it["ref"]), source="adapter")
                graph.link(d["id"], "part_of", e["id"], source="adapter")
            elif it["kind"] == "site" and it["ref"]:
                s = graph.upsert("site", it["title"] or f"Site {it['ref']}", ref=("sites", it["ref"]), source="adapter")
                graph.link(s["id"], "part_of", e["id"], source="adapter")
            elif it["kind"] == "chat" and it["ref"]:
                t = graph.upsert("thread", it["title"] or f"Chat {it['ref']}", ref=("conversations", it["ref"]), source="adapter")
                graph.link(t["id"], "part_of", e["id"], source="adapter")
            elif it["kind"] == "deadline" and it["title"]:
                t = graph.upsert("task", it["title"], {"due": it["due"], "done": bool(it["done"])}, ref=("project_items", it["id"]), source="adapter")
                graph.link(t["id"], "part_of", e["id"], source="adapter")
    return n


def project_entity(project_row_id: int) -> int | None:
    r = db.one("SELECT id FROM kn_entities WHERE ref_table = 'projects' AND ref_id = ?", str(project_row_id))
    return r["id"] if r else None


def sync_adapt_facts() -> int:
    """'What I know about you' (#29) facts become profile memories, linked back by source_ref adapt_fact:<id>."""
    if not _has("adapt_facts"):
        return 0
    from ..adapt import store

    have = {r["source_ref"] for r in db.all("SELECT source_ref FROM kn_memories WHERE source_ref LIKE 'adapt_fact:%'")}
    n = 0
    for f in db.all("SELECT * FROM adapt_facts"):
        if f"adapt_fact:{f['id']}" in have:
            continue
        content = store.open_text(f["content_enc"])
        if len(content) < 3:
            continue
        tier = "procedural" if f["category"] in ("format", "preference") else "profile"
        try:
            memory.remember(content, tier, pinned=bool(f["pinned"]), confidence=f["confidence"], source_kind="adapter",
                            source_ref=f"adapt_fact:{f['id']}", evidence=store.open_text(f["evidence_enc"]),
                            observed_at=f["created_at"])
            n += 1
        except Exception as e:  # noqa: BLE001
            log.debug("fact %s skipped: %s", f["id"], e)
    return n


def sync_tasks() -> int:
    if not _has("tasks"):
        return 0
    n = 0
    for t in db.all("SELECT * FROM tasks"):
        e = graph.upsert("task", t["title"], {"status": t["status"], "due": t["due"], "notes": t["notes"]}, ref=("tasks", t["id"]),
                         source="adapter")
        if t["project"]:
            p = graph.find(t["project"], "project")
            if p:
                graph.link(e["id"], "part_of", p["id"], source="adapter")
        n += 1
    return n


def sync_files() -> int:
    if not _has("work_files"):
        return 0
    for f in db.all("SELECT id, name, kind, summary FROM work_files"):
        e = graph.upsert("document", f["name"], {"kind": f["kind"]}, ref=("work_files", f["id"]), source="adapter")
        index.put("file", str(f["id"]), f["summary"] or "", f["name"], at=None)
    return len(db.all("SELECT id FROM work_files"))


def sync_sites() -> int:
    if not _has("sites"):
        return 0
    rows = db.all("SELECT id, name, url FROM sites")
    for s in rows:
        graph.upsert("site", s["name"], {"url": s["url"]}, ref=("sites", s["id"]), source="adapter")
    return len(rows)


def sync_notes() -> int:
    if not _has("docs"):
        return 0
    rows = db.all("SELECT id, title, body FROM docs")
    for d in rows:
        index.put("note", str(d["id"]), d["body"][:20000], d["title"] or "Note")
    return len(rows)


def sync_events() -> int:
    if not _has("events"):
        return 0
    rows = db.all("SELECT id, title, location, start FROM events ORDER BY start DESC LIMIT 2000")
    for ev in rows:
        index.put("event", str(ev["id"]), ev["location"], ev["title"], at=ev["start"])
    return len(rows)


def backfill(c: sqlite3.Connection | None = None) -> dict:
    out = {}
    for name, fn in (("projects", sync_projects), ("facts", sync_adapt_facts), ("tasks", sync_tasks), ("files", sync_files),
                     ("sites", sync_sites), ("notes", sync_notes), ("events", sync_events)):
        out[name] = fn()
    return out
