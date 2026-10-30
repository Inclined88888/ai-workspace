"""/api/knowledge: memory, people, projects, search, conflicts, tidy-up, export, data safety."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..security import require_user
from . import backup, crypto, export_all, graph, importers, improve, live, memory, schema
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


class RefileIn(BaseModel):
    project_id: int | None = None


@router.get("/filings")
def filings():
    return live.recent_filings()


@router.get("/filings/{item_kind}/{item_ref}")
def filing(item_kind: str, item_ref: str):
    return live.filing_for(item_kind, item_ref)


@router.post("/filings/{fid}/change")
def refile(fid: int, body: RefileIn):
    return live.refile_item(fid, body.project_id)


@router.post("/filings/{fid}/ok")
def ok(fid: int):
    live.confirm_filing(fid)
    return {"ok": True}


@router.post("/sync")
def sync():
    return live.sync_once()


class ToggleIn(BaseModel):
    enabled: bool


@router.get("/improvements")
def improvements():
    return improve.latest()


@router.put("/improvements/settings")
def improvements_toggle(body: ToggleIn):
    improve.set_enabled(body.enabled)
    return improve.latest()


@router.post("/improvements/run")
def improvements_run():
    return improve.run_once(force=True)


@router.post("/improvements/{pid}/{choice}")
def improvements_decide(pid: int, choice: str):
    return improve.decide(pid, choice == "approve")


@router.get("/backups")
def backups():
    return {"auto": db_setting("kn_backup_auto", "1") == "1", "backups": backup.list_()}


@router.post("/backups")
def backup_now():
    return backup.create("manual")


@router.post("/backups/{name}/restore")
def backup_restore(name: str):
    return backup.restore(name)


@router.get("/export.zip")
def export_zip():
    return Response(backup.export_zip(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=ai-workspace-export.zip"})


@router.post("/import/{source}")
async def import_from(source: str, file: UploadFile = File(...)):
    return importers.run(source, await file.read())


def db_setting(key: str, default: str) -> str:
    from .. import db

    return db.get_setting(key, default) or default
