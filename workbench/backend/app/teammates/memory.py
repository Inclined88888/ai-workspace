"""Thin adapter for a teammate's memory scope (feat/grok-style).

Everything teammates need from memory goes through these four functions, so the shared memory layer from
feat/memory-core (app/knowledge/: scopes per assistant/team, hybrid recall) can replace this local store by changing only
this file. Until then a teammate's own notes live in tm_notes, encrypted with the workspace key.

Scopes: "own"    = this teammate's notes + the shared facts about you every teammate knows (adapt store)
        "shared" = also notes every other teammate saved.
"""

from __future__ import annotations

from fastapi import HTTPException

from .. import db
from ..security import decrypt, encrypt


def _backend():
    """The memory-core interface when it exists in this build (swap point)."""
    try:
        from .. import knowledge  # noqa: F401 - feat/memory-core

        return knowledge if hasattr(knowledge, "scoped") else None
    except ImportError:
        return None


def notes(tid: int) -> list[dict]:
    return [{"id": r["id"], "content": decrypt(r["content"]), "created_at": r["created_at"]}
            for r in db.all("SELECT * FROM tm_notes WHERE teammate_id = ? ORDER BY id DESC LIMIT 200", tid)]


def add(tid: int, content: str) -> dict:
    content = (content or "").strip()[:2000]
    if not content:
        raise HTTPException(400, "Nothing to remember")
    nid = db.run("INSERT INTO tm_notes(teammate_id, content, created_at) VALUES (?, ?, ?)", tid, encrypt(content), db.now())
    return {"id": nid, "content": content}


def delete(tid: int, nid: int) -> dict:
    db.run("DELETE FROM tm_notes WHERE id = ? AND teammate_id = ?", nid, tid)
    return {"deleted": nid}


def recall(tid: int, scope: str, query: str = "", limit: int = 15) -> list[str]:
    """Notes to put in front of the model for this turn (newest first; word overlap with the message ranks first)."""
    rows = db.all("SELECT teammate_id, content FROM tm_notes WHERE teammate_id = ? OR ? = 'shared' ORDER BY id DESC LIMIT 200", tid, scope)
    items = [decrypt(r["content"]) for r in rows]
    words = {w for w in query.lower().split() if len(w) > 3}
    items.sort(key=lambda t: -len(words & set(t.lower().split())))
    return items[:limit]


def purge(tid: int) -> None:
    db.run("DELETE FROM tm_notes WHERE teammate_id = ?", tid)
