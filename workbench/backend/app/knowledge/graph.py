"""Entity graph: typed things and typed relationships."""
from __future__ import annotations

import uuid

from fastapi import HTTPException

from .. import db
from . import crypto, index, schema
from .text import norm_name

TYPES = ("person", "organisation", "project", "task", "document", "site", "event", "thread", "place", "asset", "note", "user")
RELS = ("works_at", "member_of", "assigned_to", "about", "mentions", "derived_from", "part_of", "attended", "located_at", "owns")


def _uid() -> str:
    return uuid.uuid4().hex


def _out(r: dict) -> dict:
    return {"id": r["id"], "uid": r["uid"], "type": r["type"], "name": r["name"], "attrs": crypto.open_json(r["attrs_enc"], {}),
            "ref": f"{r['ref_table']}:{r['ref_id']}" if r["ref_table"] else "", "scope": r["scope"], "privacy": r["privacy"],
            "pinned": bool(r["pinned"]), "source": r["source"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def get(eid: int) -> dict:
    schema.ensure()
    r = db.one("SELECT * FROM kn_entities WHERE id = ? AND deleted_at IS NULL", eid)
    if not r:
        raise HTTPException(404, "Not found")
    return _out(r)


def find(name: str, type: str | None = None) -> dict | None:
    """By exact normalised name or alias (email, phone, short name)."""
    schema.ensure()
    key = norm_name(name)
    if not key:
        return None
    sql = "SELECT * FROM kn_entities WHERE deleted_at IS NULL AND (name_key = ? OR id IN (SELECT entity_id FROM kn_aliases WHERE alias_key = ?))"
    args: list = [key, crypto.blind(key)]
    if type:
        sql += " AND type = ?"
        args.append(type)
    r = db.one(sql + " ORDER BY id LIMIT 1", *args)
    return _out(r) if r else None


def upsert(type: str, name: str, attrs: dict | None = None, ref: tuple[str, str] | None = None, scope: str = "shared",
           privacy: str = "normal", aliases: list[str] | None = None, source: str = "auto") -> dict:
    """Create or update. Matches an existing row by (ref_table, ref_id), then by type + name/alias (dedupe)."""
    schema.ensure()
    if type not in TYPES:
        raise HTTPException(400, f"Unknown kind of thing: {type}")
    name = " ".join((name or "").split())[:200]
    row = None
    if ref:
        row = db.one("SELECT * FROM kn_entities WHERE ref_table = ? AND ref_id = ?", ref[0], str(ref[1]))
    if not row and name:
        hit = find(name, type)
        row = db.one("SELECT * FROM kn_entities WHERE id = ?", hit["id"]) if hit else None
    for a in (attrs or {}).get("email", ""), (attrs or {}).get("phone", ""):
        if not row and a:
            hit = find(a, type)
            row = db.one("SELECT * FROM kn_entities WHERE id = ?", hit["id"]) if hit else None
    now = db.now()
    if row:
        merged = {**crypto.open_json(row["attrs_enc"], {}), **{k: v for k, v in (attrs or {}).items() if v not in (None, "")}}
        db.run("UPDATE kn_entities SET name = ?, name_key = ?, attrs_enc = ?, ref_table = COALESCE(NULLIF(?, ''), ref_table), "
               "ref_id = COALESCE(NULLIF(?, ''), ref_id), updated_at = ?, last_seen_at = ?, deleted_at = NULL WHERE id = ?",
               name or row["name"], norm_name(name or row["name"]), crypto.seal(merged), ref[0] if ref else "",
               str(ref[1]) if ref else "", now, now, row["id"])
        eid = row["id"]
    else:
        eid = db.run("INSERT INTO kn_entities(uid, type, name, name_key, attrs_enc, ref_table, ref_id, scope, privacy, source, "
                     "created_at, updated_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     _uid(), type, name, norm_name(name), crypto.seal(attrs or {}), ref[0] if ref else "",
                     str(ref[1]) if ref else "", scope, privacy, source, now, now, now)
    al = {norm_name(x) for x in [*(aliases or []), (attrs or {}).get("email", ""), (attrs or {}).get("phone", "")] if x}
    first = norm_name(name).split(" ")[0] if type == "person" and name else ""
    if first and len(first) > 2:
        al.add(first)
    for a in al:
        db.run("INSERT OR IGNORE INTO kn_aliases(entity_id, alias_key) VALUES (?, ?)", eid, crypto.blind(a))
    e = get(eid)
    index.put("entity", str(eid), " ".join(str(v) for v in e["attrs"].values()), f"{e['name']} ({type})", e["scope"], e["privacy"])
    return e


def link(src: int, rel: str, dst: int, confidence: float = 1.0, source: str = "auto") -> dict:
    schema.ensure()
    if rel not in RELS:
        raise HTTPException(400, f"Unknown relationship: {rel}")
    if src == dst:
        raise HTTPException(400, "A thing can't be linked to itself")
    db.run("INSERT INTO kn_edges(uid, src, rel, dst, confidence, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
           "ON CONFLICT(src, rel, dst) DO UPDATE SET confidence = MAX(confidence, excluded.confidence)",
           _uid(), src, rel, dst, confidence, source, db.now())
    return {"src": src, "rel": rel, "dst": dst}


def unlink(src: int, rel: str, dst: int) -> None:
    db.run("DELETE FROM kn_edges WHERE src = ? AND rel = ? AND dst = ?", src, rel, dst)


def neighbours(eid: int, depth: int = 1) -> list[dict]:
    schema.ensure()
    seen, frontier, out = {eid}, [eid], []
    for d in range(depth):
        nxt = []
        for n in frontier:
            for r in db.all("SELECT src, rel, dst FROM kn_edges WHERE src = ? OR dst = ?", n, n):
                other = r["dst"] if r["src"] == n else r["src"]
                if other in seen:
                    continue
                seen.add(other)
                nxt.append(other)
                try:
                    out.append({**get(other), "rel": r["rel"], "direction": "out" if r["src"] == n else "in", "hops": d + 1})
                except HTTPException:
                    pass
        frontier = nxt
    return out


def list_(type: str | None = None, q: str = "", limit: int = 200) -> list[dict]:
    schema.ensure()
    sql, args = "SELECT * FROM kn_entities WHERE deleted_at IS NULL", []
    if type:
        sql += " AND type = ?"
        args.append(type)
    if q:
        sql += " AND name_key LIKE ?"
        args.append(f"%{norm_name(q)}%")
    return [_out(r) for r in db.all(sql + " ORDER BY updated_at DESC LIMIT ?", *args, limit)]


def forget(eid: int) -> dict:
    """Removes the thing, its links, aliases and every memory about it (cascades)."""
    schema.ensure()
    mids = [r["id"] for r in db.all("SELECT id FROM kn_memories WHERE subject_id = ?", eid)]
    for m in mids:
        index.drop("memory", str(m))
    db.run("DELETE FROM kn_entities WHERE id = ?", eid)
    index.drop("entity", str(eid))
    return {"forgotten": eid, "memories": len(mids)}


def merge(keep: int, other: int) -> dict:
    """Duplicate clean-up: move links, aliases and memories from `other` onto `keep`, then remove `other`."""
    schema.ensure()
    k, o = get(keep), get(other)
    c = db.conn()
    c.execute("UPDATE OR IGNORE kn_edges SET src = ? WHERE src = ?", (keep, other))
    c.execute("UPDATE OR IGNORE kn_edges SET dst = ? WHERE dst = ?", (keep, other))
    c.execute("UPDATE OR IGNORE kn_aliases SET entity_id = ? WHERE entity_id = ?", (keep, other))
    c.execute("UPDATE kn_memories SET subject_id = ? WHERE subject_id = ?", (keep, other))
    c.execute("UPDATE kn_memories SET project_id = ? WHERE project_id = ?", (keep, other))
    c.execute("INSERT OR IGNORE INTO kn_aliases(entity_id, alias_key) VALUES (?, ?)", (keep, crypto.blind(norm_name(o["name"]))))
    db.run("UPDATE kn_entities SET attrs_enc = ? WHERE id = ?", crypto.seal({**o["attrs"], **k["attrs"]}), keep)
    db.run("DELETE FROM kn_entities WHERE id = ?", other)
    index.drop("entity", str(other))
    return {"kept": keep, "merged": other}


def duplicates(limit: int = 50) -> list[dict]:
    """Tidy-up suggestions: same type, one name contained in the other or same first+last word."""
    ents = list_(limit=2000)
    out, by = [], {}
    for e in ents:
        by.setdefault(e["type"], []).append(e)
    for t, es in by.items():
        for i, a in enumerate(es):
            for b in es[i + 1:]:
                ka, kb = norm_name(a["name"]), norm_name(b["name"])
                if ka and kb and ka != kb and (ka in kb or kb in ka) and min(len(ka), len(kb)) >= 4:
                    out.append({"type": t, "a": a, "b": b, "why": f"“{a['name']}” and “{b['name']}” look like the same {t}"})
    return out[:limit]
