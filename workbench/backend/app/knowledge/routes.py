"""/api/knowledge: memory, people, projects, search, conflicts, tidy-up, export, data safety."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..security import require_user
from . import crypto, export_all, graph, memory, schema
from .recall import recall as _recall_fn, search_all

router = APIRouter(prefix="/api/knowledge", dependencies=[Depends(require_user)])


class MemIn(BaseModel):
    content: str
    tier: str = "profile"
    subject_id: int | None = None
    project_id: int | None = None
    scope: str = "shared"
    privacy: str = "normal"
    pinned: bool = False


class MemPatch(BaseModel):
    content: str | None = None
    pinned: bool | None = None
    privacy: str | None = None
    scope: str | None = None
    tier: str | None = None


class EntIn(BaseModel):
    type: str
    name: str
    attrs: dict = {}
    scope: str = "shared"


class LinkIn(BaseModel):
    src: int
    rel: str
    dst: int


@router.get("/memories")
def list_memories(tier: str | None = None, status: str = "active"):
    return memory.list_(tier=tier, status=status)


@router.post("/memories")
def add_memory(body: MemIn):
    return memory.remember(body.content, body.tier, subject_id=body.subject_id, project_id=body.project_id, scope=body.scope,
                           privacy=body.privacy, pinned=body.pinned, confidence=1.0, source_kind="user")


@router.patch("/memories/{mid}")
def edit_memory(mid: int, body: MemPatch):
    return memory.update(mid, **body.model_dump())


@router.delete("/memories/{mid}")
def forget_memory(mid: int):
    return memory.forget(mid)


@router.get("/memories/{mid}/why")
def why(mid: int):
    return memory.why(mid)


@router.get("/conflicts")
def conflicts():
    return memory.conflicts()


@router.post("/conflicts/{cid}/{keep}")
def resolve(cid: int, keep: str):
    return memory.resolve(cid, keep)


@router.get("/recall")
def do_recall(q: str, k: int = 10, project_id: int | None = None):
    return _recall_fn(q, k=k, project_id=project_id)


@router.get("/search")
def search(q: str, limit: int = 30):
    return search_all(q, limit=limit)


@router.get("/entities")
def entities(type: str | None = None, q: str = ""):
    return graph.list_(type, q)


@router.post("/entities")
def add_entity(body: EntIn):
    return graph.upsert(body.type, body.name, body.attrs, scope=body.scope, source="user")


@router.get("/entities/{eid}")
def entity(eid: int):
    e = graph.get(eid)
    return {**e, "links": graph.neighbours(eid, 1), "memories": memory.list_(subject_id=eid) + memory.list_(project_id=eid)}


@router.delete("/entities/{eid}")
def forget_entity(eid: int):
    return graph.forget(eid)


@router.post("/links")
def add_link(body: LinkIn):
    return graph.link(body.src, body.rel, body.dst, source="user")


@router.get("/tidy")
def tidy():
    return {"duplicates": graph.duplicates(), "conflicts": memory.conflicts()}


@router.post("/merge/{keep}/{other}")
def merge(keep: int, other: int):
    return graph.merge(keep, other)


@router.get("/export")
def export():
    return JSONResponse(export_all(), headers={"Content-Disposition": "attachment; filename=knowledge-export.json"})


@router.get("/status")
def status():
    return {"encryption": crypto.status(), "integrity": schema.integrity()}
